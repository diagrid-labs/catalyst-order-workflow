"""Restock-order workflow.

Registers the reposition order (writing the placed record to state and
appending the order id to the active-orders list) and then starts the
delivery workflow as a child. The parent workflow only completes once the
child has — i.e. once the delivery is confirmed — so callers that need
the order id sooner should read it from state after registration rather
than awaiting the workflow.
"""

import logging
from datetime import datetime, timezone

import dapr.ext.workflow as wf
from dapr.clients import DaprClient

from order_state import (
    read_active_orders,
    write_active_orders,
    write_order,
)

from .delivery import delivery_workflow

logger = logging.getLogger("inventory.workflows.restock_order")


def restock_order_workflow(ctx: wf.DaprWorkflowContext, order: dict):
    """Register the order, then hand off to the delivery workflow."""
    record = yield ctx.call_activity(register_order, input=order)
    delivery_id = f"delivery_{order['id']}"
    yield ctx.call_child_workflow(
        delivery_workflow, input=order, instance_id=delivery_id
    )
    return record


def register_order(_: wf.WorkflowActivityContext, order: dict) -> dict:
    placed_at = datetime.now(timezone.utc)
    record = {
        "id": order["id"],
        "items": order["items"],
        "placed_at": placed_at.isoformat(),
        "status": "placed",
        "delivered_at": None,
    }
    with DaprClient() as d:
        write_order(d, record)
        active = read_active_orders(d)
        if record["id"] not in active:
            active.append(record["id"])
            write_active_orders(d, active)
    logger.info(f"Registered reposition order {record['id']}: {order['items']}")
    return record
