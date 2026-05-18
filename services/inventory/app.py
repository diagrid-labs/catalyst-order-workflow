import json
import logging
import os
from uuid import uuid4

import dapr.ext.workflow as wf
import grpc
from dapr.clients import DaprClient
from dapr.clients.grpc._request import TransactionalStateOperation
from dapr.clients.grpc._state import StateItem
from flask import Flask, jsonify, make_response, request

from order_state import (
    STATESTORE_NAME,
    now_iso,
    read_active_orders,
    read_order,
)
from sales_state import (
    HORIZON_MIN,
    SMA_WINDOW_MIN,
    _decode_history,
    encode_appended,
    forecast,
    sales_key,
    summarize,
)
from workflows import (
    deliver_order,
    delivery_workflow,
    register_order,
    restock_order_workflow,
)

APP_PORT = int(os.getenv("APP_PORT", 3002))
PUBSUB_NAME = os.getenv("PUBSUB_NAME", "pubsub")
SALES_TOPIC = os.getenv("SALES_TOPIC", "sales")

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
    """Decrement stock by `qty` and append a sale to history in one transaction.

    Returns (new_stock, error) where error is one of None, "not_found",
    "out_of_stock", or "conflict". Both writes share a single transaction with
    per-key etags; if either etag is stale the transaction aborts and we retry,
    so concurrent reservations cannot double-decrement nor drop a sale entry.
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

        history_resp = d.get_state(STATESTORE_NAME, sales_key(sku_key))
        history = _decode_history(history_resp)
        encoded = encode_appended(history, qty, now_iso())

        try:
            d.execute_state_transaction(
                store_name=STATESTORE_NAME,
                operations=[
                    TransactionalStateOperation(
                        key=sku_key,
                        data=str(stock - qty),
                        etag=stock_resp.etag or None,
                    ),
                    TransactionalStateOperation(
                        key=sales_key(sku_key),
                        data=encoded,
                        etag=history_resp.etag or None,
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

            d.publish_event(
                pubsub_name=PUBSUB_NAME,
                topic_name=SALES_TOPIC,
                data=json.dumps({
                    "sku": item.lower(),
                    "qty": qty,
                    "ts": now_iso(),
                    "order_id": order_id,
                }),
                data_content_type="application/json",
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


@app.route("/api/v1/inventory/sales/history", methods=["GET"])
def get_sales_history():
    sku = request.args.get("sku")
    if not sku:
        return make_response(jsonify({"error": "sku required"}), 400)
    window_min = int(request.args.get("window_min", SMA_WINDOW_MIN))
    with DaprClient() as d:
        return jsonify(summarize(d, sku, window_min))


@app.route("/api/v1/inventory/forecast", methods=["GET"])
def get_forecast():
    sku = request.args.get("sku")
    if not sku:
        return make_response(jsonify({"error": "sku required"}), 400)
    horizon_min = int(request.args.get("horizon_min", HORIZON_MIN))
    window_min = int(request.args.get("window_min", SMA_WINDOW_MIN))
    with DaprClient() as d:
        return jsonify(forecast(d, sku, horizon_min, window_min))


@app.route("/api/v1/inventory/orders", methods=["POST"])
def create_reposition_order():
    if not request.is_json:
        return make_response(jsonify({"error": "Request must be JSON"}), 400)
    body = request.json or {}
    items = body.get("items") or {}
    if not items:
        return make_response(jsonify({"error": "items required"}), 400)

    order_id = f"reposition_{uuid4().hex[:8]}"
    wf_client = wf.DaprWorkflowClient()
    wf_client.schedule_new_workflow(
        restock_order_workflow,
        input={"id": order_id, "items": items},
        instance_id=order_id,
    )

    try:
        wf_client.wait_for_workflow_completion(order_id, timeout_in_seconds=10)
    except Exception as e:
        logger.warning(f"Order {order_id} placement wait failed (will keep running): {e}")

    with DaprClient() as d:
        record = read_order(d, order_id)

    if record is None:
        record = {"id": order_id, "items": items, "status": "placed"}
    return jsonify(record), 201


@app.route("/api/v1/inventory/orders/active", methods=["GET"])
def list_active_orders():
    with DaprClient() as d:
        active_ids = read_active_orders(d)
        records = [read_order(d, oid) for oid in active_ids]
        records = [r for r in records if r is not None]
    return jsonify(records)


@app.route("/api/v1/inventory/orders/<order_id>/confirm", methods=["POST"])
def confirm_reposition_order(order_id):
    """Notify the delivery workflow that the reposition has arrived.

    Body (all fields optional): `{message: str, amendments: {sku: qty}}`.
    If `amendments` is provided, the amended quantities are applied to stock
    instead of the originally ordered ones. Returns 202 once the event is
    raised; the workflow runs the inventory update asynchronously.
    """
    body = request.json if request.is_json else {}
    delivery_id = f"delivery_{order_id}"
    try:
        wf.DaprWorkflowClient().raise_workflow_event(
            instance_id=delivery_id,
            event_name="delivery_confirmed",
            data=body or {},
        )
    except Exception as e:
        logger.error(f"Failed to raise delivery_confirmed for {order_id}: {e}")
        return make_response(
            jsonify({"error": "Internal Server Error", "message": str(e)}), 500
        )
    return jsonify({"order_id": order_id, "status": "confirmation_sent"}), 202


@app.route("/api/v1/inventory/orders/<order_id>", methods=["GET"])
def get_reposition_order(order_id):
    with DaprClient() as d:
        record = read_order(d, order_id)
    if record is None:
        return make_response(jsonify({"error": "not found"}), 404)
    return jsonify(record)


@app.route("/health", methods=["GET"])
@app.route("/healthz", methods=["GET"])
def health_check():
    return jsonify({
        "service": "inventory-service",
        "status": "healthy",
        "version": "1.1.0",
    })


def main():
    logger.info("Starting inventory workflow runtime...")
    wf_runtime = wf.WorkflowRuntime()
    wf_runtime.register_workflow(restock_order_workflow)
    wf_runtime.register_workflow(delivery_workflow)
    wf_runtime.register_activity(register_order)
    wf_runtime.register_activity(deliver_order)
    wf_runtime.start()

    app.run(host="0.0.0.0", port=APP_PORT, debug=False, use_reloader=False)

    wf_runtime.shutdown()


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s.%(msecs)03d %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO,
    )
    main()
