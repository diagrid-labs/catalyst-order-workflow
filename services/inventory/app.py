import logging
import os

import grpc
from dapr.clients import DaprClient
from dapr.clients.grpc._request import TransactionalStateOperation
from dapr.clients.grpc._state import StateItem
from flask import Flask, jsonify, make_response, request

APP_PORT = int(os.getenv("APP_PORT", 3002))
STATESTORE_NAME = os.getenv("STATESTORE_NAME", "statestore")

app = Flask(__name__)

logger = logging.getLogger("inventory_service")

INVENTORY_ITEMS = ["orange", "apple", "pear", "kiwi"]


class InventoryItem:
    def __init__(self, item: str, quantity: int):
        self.item = item
        self.quantity = quantity

    def to_dict(self):
        return {"name": self.item, "quantity": self.quantity}


@app.route("/api/v1/inventory", methods=["GET"])
def get_inventory():
    inventory = []
    with DaprClient() as d:
        try:
            for item_key in INVENTORY_ITEMS:
                item = d.get_state(STATESTORE_NAME, item_key.lower())
                if item.data:
                    value = item.data.decode("utf-8")
                    inventory.append(InventoryItem(item=item_key, quantity=value).to_dict())
            if not inventory:
                return jsonify({"message": "No inventory available."})
            return jsonify(inventory)
        except grpc.RpcError as err:
            logger.error(f"Error retrieving inventory: {err.details()}")
            return make_response(
                jsonify({"error": "Internal Server Error",
                         "message": "Failed to retrieve inventory"}), 500
            )


@app.route("/api/v1/inventory", methods=["DELETE"])
def clear_inventory():
    with DaprClient() as d:
        try:
            for item_key in INVENTORY_ITEMS:
                d.delete_state(STATESTORE_NAME, item_key.lower())
            logger.info("Inventory cleared successfully")
            return jsonify({"message": "Inventory has been cleared."})
        except grpc.RpcError as err:
            logger.error(f"Error clearing inventory: {err.details()}")
            return make_response(
                jsonify({"error": "Internal Server Error",
                         "message": "Failed to clear inventory"}), 500
            )


@app.route("/api/v1/inventory/restock", methods=["POST"])
def restock_inventory():
    """Destructively set every SKU to 100. Contrast with `/reposition`,
    which is additive.
    """
    try:
        with DaprClient() as d:
            state_items = [
                StateItem(key=item_key, value="100", etag="", metadata={}, options=None)
                for item_key in INVENTORY_ITEMS
            ]
            d.save_bulk_state(store_name=STATESTORE_NAME, states=state_items)
        logger.info("Inventory restocked (reset to 100 for all SKUs)")
        return jsonify({"message": "Inventory has been restocked."})
    except grpc.RpcError as err:
        logger.error(f"Error restocking inventory: {err.details()}")
        return make_response(
            jsonify({"error": "Internal Server Error",
                     "message": "Failed to restock inventory"}), 500
        )


@app.route("/api/v1/inventory/reposition", methods=["POST"])
def reposition_inventory():
    """Increase stock by the given quantities. Contrast with `/restock`,
    which destructively resets every SKU to a fixed value.
    """
    if not request.is_json:
        return make_response(jsonify({"error": "Request must be JSON"}), 400)
    body = request.json or {}
    items = body.get("items") or {}
    if not items:
        return make_response(jsonify({"error": "items required"}), 400)

    updated = {}
    with DaprClient() as d:
        for sku, qty in items.items():
            resp = d.get_state(STATESTORE_NAME, sku.lower())
            current = int(resp.data.decode("utf-8")) if resp.data else 0
            new_qty = current + int(qty)
            d.save_state(STATESTORE_NAME, sku.lower(), str(new_qty))
            updated[sku] = new_qty
    return jsonify({"updated": updated})


RESERVE_MAX_RETRIES = 5


def _reserve_atomic(d: DaprClient, sku: str, qty: int = 1):
    """Decrement stock by `qty` using an etag-guarded transaction.

    Returns (new_stock, error) where error is one of None, "not_found",
    "out_of_stock", or "conflict". The decrement is etag-guarded so two
    concurrent reservations cannot both succeed against the same prior
    stock value; if the etag is stale the transaction aborts and we retry,
    giving up with "conflict" after RESERVE_MAX_RETRIES.
    """
    sku_key = sku.lower()
    for _ in range(RESERVE_MAX_RETRIES):
        stock_resp = d.get_state(STATESTORE_NAME, sku_key)
        if not stock_resp.data:
            return None, "not_found"
        try:
            stock = int(stock_resp.data.decode("utf-8"))
        except (AttributeError, ValueError):
            return None, "not_found"
        if stock < qty:
            return None, "out_of_stock"
        try:
            d.execute_state_transaction(
                store_name=STATESTORE_NAME,
                operations=[
                    TransactionalStateOperation(
                        key=sku_key,
                        data=str(stock - qty),
                        etag=stock_resp.etag or None,
                    ),
                ],
            )
            return stock - qty, None
        except grpc.RpcError as err:
            logger.info(f"Retrying reservation for {sku_key}: {err.details()}")
    return None, "conflict"


def _parse_reserve_order(payload):
    """Validate a reserve request body. Returns (order_id, item, qty, error)
    where `error` is a Flask response on failure and None on success.
    """
    def bad(msg):
        return None, None, None, make_response(
            jsonify({"error": "Bad Request", "message": msg}), 400
        )

    if not isinstance(payload, dict):
        return bad("Invalid order format")
    order_id = payload.get("id")
    item = payload.get("item")
    if not order_id or not item:
        return bad("Invalid order format")
    if not isinstance(item, str):
        return bad("item must be a string")
    try:
        qty = int(payload.get("qty", 1))
    except (TypeError, ValueError):
        return bad("qty must be an integer")
    if qty < 1:
        return bad("qty must be >= 1")
    return order_id, item, qty, None


@app.route("/api/v1/inventory/reserve", methods=["POST"])
def reserve_inventory():
    if not request.is_json:
        return make_response(
            jsonify({"error": "Bad Request", "message": "Request must be JSON"}), 400
        )
    order_id, item, qty, err_resp = _parse_reserve_order(request.json)
    if err_resp is not None:
        return err_resp

    logger.info(f"Processing inventory reservation for order {order_id}: {item} x{qty}")

    try:
        with DaprClient() as d:
            new_stock, err = _reserve_atomic(d, item, qty=qty)
            if err == "not_found":
                return jsonify({"id": order_id, "success": False,
                                "message": f"Item {item} not found in inventory"})
            if err == "out_of_stock":
                return jsonify({"id": order_id, "success": False,
                                "message": f"Item {item} is out of stock"})
            if err == "conflict":
                return make_response(
                    jsonify({"id": order_id, "success": False,
                             "message": "Too much contention; please retry"}),
                    503,
                )
            return make_response(jsonify({
                "id": order_id, "success": True,
                "message": "Item reserved successfully",
                "remaining": new_stock,
            }))
    except grpc.RpcError as err:
        logger.error(f"Error reserving inventory: {err.details()}")
        return make_response(
            jsonify({"error": "Internal Server Error",
                     "message": "Failed to reserve inventory"}), 500
        )
    except Exception as e:
        logger.error(f"Unexpected error reserving inventory: {str(e)}")
        return make_response(
            jsonify({"error": "Internal Server Error",
                     "message": f"Unexpected error: {str(e)}"}), 500
        )


@app.route("/health", methods=["GET"])
@app.route("/healthz", methods=["GET"])
def health_check():
    return jsonify({
        "service": "inventory-service",
        "status": "healthy",
        "version": "1.0.0",
    })


def main():
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s.%(msecs)03d %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO,
    )
    main()
