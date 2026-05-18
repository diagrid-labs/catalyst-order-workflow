"""Delivery workflow.

Waits for an external `delivery_confirmed` event, then applies the order's
stock and marks the order delivered. The event payload may include
amendments (a per-SKU quantity override) and an optional human-readable
message; if amendments are provided, the amended quantities are applied
instead of the originally ordered ones.
"""

import logging

import dapr.ext.workflow as wf
from dapr.clients import DaprClient

from order_state import (
    STATESTORE_NAME,
    now_iso,
    read_active_orders,
    read_order,
    write_active_orders,
    write_order,
)

logger = logging.getLogger("inventory.workflows.delivery")

DELIVERY_CONFIRMED_EVENT = "delivery_confirmed"


def delivery_workflow(ctx: wf.DaprWorkflowContext, order: dict):
    """Wait for `delivery_confirmed`, then apply the (optionally amended) stock."""
    confirmation = yield ctx.wait_for_external_event(DELIVERY_CONFIRMED_EVENT)
    confirmation = confirmation or {}
    amendments = confirmation.get("amendments") or {}
    delivered_items = amendments if amendments else order["items"]
    result = yield ctx.call_activity(
        deliver_order,
        input={
            "id": order["id"],
            "items": delivered_items,
            "confirmation": confirmation,
        },
    )
    return result


def deliver_order(_: wf.WorkflowActivityContext, payload: dict) -> dict:
    """Add `items` to inventory and mark the order delivered.

    `payload` carries the order id, the items to apply (already chosen between
    original and amended in the workflow), and the raw confirmation envelope
    so the message and amendments can be persisted on the order record.

    Per-SKU stock updates are read-modify-write without an etag — fine for
    the demo where deliveries don't overlap, but two concurrent deliveries
    touching the same SKU could lose an update. /reserve is the path that
    needs to be concurrency-safe and is etag-guarded there.
    """
    order_id = payload["id"]
    delivered_items = payload["items"]
    confirmation = payload.get("confirmation") or {}

    with DaprClient() as d:
        for sku, qty in delivered_items.items():
            resp = d.get_state(STATESTORE_NAME, sku.lower())
            current = int(resp.data.decode("utf-8")) if resp.data else 0
            d.save_state(STATESTORE_NAME, sku.lower(), str(current + int(qty)))

        record = read_order(d, order_id)
        if record is not None:
            record["status"] = "delivered"
            record["delivered_at"] = now_iso()
            record["delivered_items"] = delivered_items
            if confirmation.get("message"):
                record["confirmation_message"] = confirmation["message"]
            if confirmation.get("amendments"):
                record["amendments"] = confirmation["amendments"]
            write_order(d, record)

        active = read_active_orders(d)
        active = [oid for oid in active if oid != order_id]
        write_active_orders(d, active)

    logger.info(f"Delivered reposition order {order_id}: {delivered_items}")
    return record or {"id": order_id, "status": "delivered"}
