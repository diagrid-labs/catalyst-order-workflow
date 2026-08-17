import json
import logging
import os

import grpc
from dapr.clients import DaprClient
from dapr.clients.grpc._state import StateItem
from flask import Flask, jsonify, make_response, request

from sales_state import (
    HORIZON_MIN,
    SMA_WINDOW_MIN,
    _decode_history,
    encode_appended,
    forecast,
    sales_key,
    summarize,
)
from state import STATESTORE_NAME, now_iso

APP_PORT = int(os.getenv("APP_PORT", 3002))
PUBSUB_NAME = os.getenv("PUBSUB_NAME", "pubsub")
SALES_TOPIC = os.getenv("SALES_TOPIC", "sales")

app = Flask(__name__)

logger = logging.getLogger("inventory_service")

INVENTORY_ITEMS = {
    "orange": 1.50,
    "apple": 2.00,
    "pear": 1.75,
    "kiwi": 3.00,
}


class InventoryItem:
    def __init__(self, item: str, quantity: int, price: float):
        self.item = item
        self.quantity = quantity
        self.price = price

    def to_dict(self):
        return {"name": self.item, "quantity": self.quantity, "price": self.price}


@app.route("/api/v1/inventory", methods=["GET"])
def get_inventory():
    inventory = []
    with DaprClient() as d:
        try:
            for item_key, price in INVENTORY_ITEMS.items():
                item = d.get_state(STATESTORE_NAME, item_key.lower())
                if item.data:
                    value = item.data.decode("utf-8")
                    inventory.append(
                        InventoryItem(item=item_key, quantity=value, price=price).to_dict()
                    )
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
    """Destructively set every SKU's quantity to 100. Prices are catalog data
    and are not touched. Contrast with `/reposition`, which is additive.
    """
    try:
        with DaprClient() as d:
            state_items = [
                StateItem(key=item_key, value="100", etag="", metadata={}, options=None)
                for item_key in INVENTORY_ITEMS
            ]
            d.save_bulk_state(store_name=STATESTORE_NAME, states=state_items)
        logger.info("Inventory restocked (quantity reset to 100 for all SKUs; prices unchanged)")
        return jsonify({"message": "Inventory has been restocked."})
    except grpc.RpcError as err:
        logger.error(f"Error restocking inventory: {err.details()}")
        return make_response(
            jsonify({"error": "Internal Server Error",
                     "message": "Failed to restock inventory"}), 500
        )


@app.route("/api/v1/inventory/reposition", methods=["POST"])
def reposition_inventory():
    """Increase stock quantity by the given amounts. Prices are catalog data
    and are not touched. Contrast with `/restock`, which destructively resets
    every SKU's quantity to a fixed value.
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

# A pending reservation must outlive the longest possible order workflow; the
# order-processor's approval gate alone allows 24 hours. Terminal records only
# exist to absorb retried calls, and their absence already means "do nothing",
# so they are kept for much less time.
PENDING_TTL_SECONDS = int(os.getenv("RESERVATION_PENDING_TTL_SECONDS", 48 * 60 * 60))
TERMINAL_TTL_SECONDS = int(os.getenv("RESERVATION_TERMINAL_TTL_SECONDS", 60 * 60))


def _reservation_key(order_id: str) -> str:
    return f"reservation:{order_id.lower()}"


def _read_reservation(d: DaprClient, order_id: str):
    """Return (record, etag) for an order's reservation, (None, None) if absent."""
    resp = d.get_state(STATESTORE_NAME, _reservation_key(order_id))
    if not resp.data:
        return None, None
    try:
        return json.loads(resp.data.decode("utf-8")), resp.etag or None
    except (ValueError, AttributeError):
        return None, None


def _write_reservation(d: DaprClient, record: dict, ttl_seconds: int, etag: str = None):
    d.save_state(
        STATESTORE_NAME,
        _reservation_key(record["order_id"]),
        json.dumps(record),
        etag=etag,
        state_metadata={"ttlInSeconds": str(ttl_seconds)},
    )


def _adjust_stock(d: DaprClient, sku_key: str, delta: int):
    """Apply `delta` to a SKU's quantity under an etag compare-and-swap.

    Returns (new_quantity, error) where error is one of None, "not_found" (the
    SKU is not stocked), "out_of_stock" (the decrement would go negative) or
    "conflict" (too many concurrent writers).
    """
    for _ in range(RESERVE_MAX_RETRIES):
        resp = d.get_state(STATESTORE_NAME, sku_key)
        if not resp.data:
            return None, "not_found"
        try:
            quantity = int(resp.data.decode("utf-8"))
        except (AttributeError, ValueError):
            return None, "not_found"

        new_quantity = quantity + delta
        if new_quantity < 0:
            return None, "out_of_stock"

        try:
            d.save_state(
                STATESTORE_NAME, sku_key, str(new_quantity), etag=resp.etag or None
            )
            return new_quantity, None
        except grpc.RpcError as err:
            logger.info(f"Retrying stock update for {sku_key}: {err.details()}")
    return None, "conflict"


def _reserve(d: DaprClient, order_id: str, sku: str, qty: int = 1):
    """Take `qty` off `sku` and record a pending reservation for `order_id`.

    Returns (remaining, error) with error one of None, "not_found",
    "out_of_stock" or "conflict". A reservation is provisional: it moves stock
    but records no sale. `/confirm` turns it into a sale, `/release` gives the
    stock back. A repeated call for the same order id short-circuits on the
    existing record, so stock moves once per order.

    The decrement and the record are two independent writes. A crash between
    them leaves the stock taken with no record to release it; the opposite
    order would instead let a later release credit stock that was never taken,
    which is worse. `/restock` resets every quantity and clears the leak.
    """
    record, _ = _read_reservation(d, order_id)
    if record is not None:
        return record.get("remaining"), None

    sku_key = sku.lower()
    remaining, err = _adjust_stock(d, sku_key, -qty)
    if err is not None:
        return None, err

    _write_reservation(d, {
        "order_id": order_id,
        "status": "pending",
        "sku": sku_key,
        "qty": qty,
        "remaining": remaining,
        "reserved_at": now_iso(),
    }, PENDING_TTL_SECONDS)
    return remaining, None


def _confirm(d: DaprClient, order_id: str):
    """Turn a pending reservation into a sale.

    Returns (record, error) with error one of None, "not_found", "released"
    (the stock was already given back) or "conflict". The record is marked
    sold before the sale is published, so a retried confirmation cannot append
    the same sale twice; the cost is that a publish failure loses the history
    entry, which only affects forecasting.
    """
    for _ in range(RESERVE_MAX_RETRIES):
        record, etag = _read_reservation(d, order_id)
        if record is None:
            return None, "not_found"
        if record["status"] == "sold":
            return record, None
        if record["status"] == "released":
            return record, "released"

        sold = {**record, "status": "sold", "sold_at": now_iso()}
        try:
            _write_reservation(d, sold, TERMINAL_TTL_SECONDS, etag=etag)
        except grpc.RpcError as err:
            logger.info(f"Retrying confirmation for {order_id}: {err.details()}")
            continue

        d.publish_event(
            pubsub_name=PUBSUB_NAME,
            topic_name=SALES_TOPIC,
            data=json.dumps({
                "sku": sold["sku"],
                "qty": sold["qty"],
                "ts": sold["sold_at"],
                "order_id": order_id,
            }),
            data_content_type="application/json",
        )
        return sold, None
    return None, "conflict"


def _release(d: DaprClient, order_id: str):
    """Give a pending reservation's stock back.

    Returns (remaining, error) with error one of None, "not_found" (nothing
    was ever reserved for this order, or the record has expired), "sold",
    "missing_sku" or "conflict". `remaining` is the quantity after the credit,
    or None when there was nothing to do.

    The record is claimed first so that concurrent releases cannot both credit
    the stock; as in `_reserve`, a crash between the two writes loses stock
    rather than inventing it.
    """
    for _ in range(RESERVE_MAX_RETRIES):
        record, etag = _read_reservation(d, order_id)
        if record is None:
            return None, "not_found"
        if record["status"] == "released":
            return None, None
        if record["status"] == "sold":
            return None, "sold"

        released = {**record, "status": "released", "released_at": now_iso()}
        try:
            _write_reservation(d, released, TERMINAL_TTL_SECONDS, etag=etag)
        except grpc.RpcError as err:
            logger.info(f"Retrying release for {order_id}: {err.details()}")
            continue

        remaining, err = _adjust_stock(d, record["sku"], int(record["qty"]))
        if err == "not_found":
            return None, "missing_sku"
        if err is not None:
            return None, err
        return remaining, None
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
    """Provisionally take an order's items out of stock.

    The reservation records no sale: the caller confirms it with `/confirm`
    once the order has gone through, or gives the stock back with `/release`.
    """
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
            new_stock, err = _reserve(d, order_id, item, qty=qty)
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


def _order_id_from_request():
    """Pull the order id out of a confirm/release body, or return an error
    response. Both endpoints accept the full order and ignore the extra fields.
    """
    if not request.is_json:
        return None, make_response(
            jsonify({"error": "Bad Request", "message": "Request must be JSON"}), 400
        )
    order_id = (request.json or {}).get("id")
    if not order_id:
        return None, make_response(
            jsonify({"error": "Bad Request", "message": "order id required"}), 400
        )
    return order_id, None


@app.route("/api/v1/inventory/confirm", methods=["POST"])
def confirm_inventory():
    """Turn an order's reservation into a sale.

    Called once the order has gone through. Idempotent by order id: a second
    confirmation reports the first one's outcome. Body: `{id: <order_id>}`.
    """
    order_id, err_resp = _order_id_from_request()
    if err_resp is not None:
        return err_resp

    logger.info(f"Confirming inventory reservation for order {order_id}")

    try:
        with DaprClient() as d:
            record, err = _confirm(d, order_id)
            if err == "not_found":
                return jsonify({"id": order_id, "success": False,
                                "message": "No reservation to confirm",
                                "remaining": None})
            if err == "released":
                return jsonify({"id": order_id, "success": False,
                                "message": "Reservation was already released",
                                "remaining": None})
            if err == "conflict":
                return make_response(
                    jsonify({"id": order_id, "success": False,
                             "message": "Too much contention; please retry",
                             "remaining": None}),
                    503,
                )
            return jsonify({
                "id": order_id, "success": True,
                "message": "Reservation confirmed",
                "remaining": record.get("remaining"),
            })
    except grpc.RpcError as err:
        logger.error(f"Error confirming reservation: {err.details()}")
        return make_response(
            jsonify({"error": "Internal Server Error",
                     "message": "Failed to confirm reservation"}), 500
        )


@app.route("/api/v1/inventory/release", methods=["POST"])
def release_inventory():
    """Compensating endpoint for `/reserve`.

    Idempotent by order id: releasing without a prior reservation is a no-op,
    and so is a second release for the same order. Body: `{id: <order_id>}`.
    """
    order_id, err_resp = _order_id_from_request()
    if err_resp is not None:
        return err_resp

    logger.info(f"Processing inventory release for order {order_id}")

    try:
        with DaprClient() as d:
            new_stock, err = _release(d, order_id)
            if err == "conflict":
                return make_response(
                    jsonify({"id": order_id, "success": False,
                             "message": "Too much contention; please retry"}),
                    503,
                )
            if err == "missing_sku":
                return make_response(
                    jsonify({"id": order_id, "success": False,
                             "message": "Reserved sku no longer in catalog"}),
                    409,
                )
            if err == "sold":
                return make_response(
                    jsonify({"id": order_id, "success": False,
                             "message": "Reservation was already sold"}),
                    409,
                )
            return jsonify({
                "id": order_id, "success": True,
                "message": "Reservation released" if new_stock is not None
                           else "Nothing to release",
                "remaining": new_stock,
            })
    except grpc.RpcError as err:
        logger.error(f"Error releasing inventory: {err.details()}")
        return make_response(
            jsonify({"error": "Internal Server Error",
                     "message": "Failed to release inventory"}), 500
        )


@app.route("/sales", methods=["POST"])
def on_sale():
    """Append a confirmed sale to its SKU's history.

    Subscribed to the `sales` topic, which `/confirm` publishes to. The history
    is the only key written here, under an etag compare-and-swap; a conflicting
    write asks Dapr to redeliver instead of retrying in-process.
    """
    envelope = request.json or {}
    data = envelope.get("data", envelope)
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            logger.error("Dropping sale event: data is not JSON")
            return jsonify({"status": "DROP"})

    sku = (data.get("sku") or "").lower()
    if not sku:
        logger.error("Dropping sale event: no sku")
        return jsonify({"status": "DROP"})
    qty = int(data.get("qty", 1))
    ts = data.get("ts") or now_iso()

    with DaprClient() as d:
        resp = d.get_state(STATESTORE_NAME, sales_key(sku))
        try:
            d.save_state(
                STATESTORE_NAME,
                sales_key(sku),
                encode_appended(_decode_history(resp), qty, ts),
                etag=resp.etag or None,
            )
        except grpc.RpcError as err:
            logger.info(f"Sale append for {sku} conflicted, redelivering: {err.details()}")
            return jsonify({"status": "RETRY"})

    return jsonify({"status": "SUCCESS"})


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


@app.route("/health", methods=["GET"])
@app.route("/healthz", methods=["GET"])
def health_check():
    return jsonify({
        "service": "inventory-service",
        "status": "healthy",
        "version": "1.1.0",
    })


def main():
    logger.info("Starting inventory service...")
    app.run(host="0.0.0.0", port=APP_PORT, debug=False, use_reloader=False)


if __name__ == "__main__":
    logging.basicConfig(
        format="%(asctime)s.%(msecs)03d %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO,
    )
    main()
