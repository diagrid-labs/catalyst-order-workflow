"""Order processing workflow.

Prices an order, reserves the stock, takes the money, gets the goods out, and
records the sale. Each step that can be undone registers a compensation as it
succeeds, and one handler unwinds them in reverse if anything later fails.

Payment and fulfilment belong to the services that perform them, so they run as
child workflows in the payments and shipping apps rather than as activities
here. `app.py` exposes the REST API that starts and inspects an order.
"""

import json
import logging
import os
from dataclasses import asdict
from datetime import timedelta

import dapr.ext.workflow as wf
from dapr.clients import DaprClient

import notifications
from order_processor.models import Approval, InventoryResult, Order, OrderResult, to_order

APPROVAL_THRESHOLD = 1000.0
APPROVAL_TIMEOUT = timedelta(hours=24)

# Fulfilment and payment are owned by the services that do them, each hosting
# its own workflow. Both are called by name across the app boundary.
SHIPPING_APP_ID = os.getenv("SHIPPING_APP_ID", "shipping")
DELIVERY_WORKFLOW = "delivery_workflow"

PAYMENTS_APP_ID = os.getenv("PAYMENTS_APP_ID", "payments")
PAYMENT_WORKFLOW = "payment_workflow"


class OrderFailed(Exception):
    """An order that cannot go through for a business reason: out of stock,
    rejected, past its approval deadline, or declined by payments.

    Raised so that every failure leaves the happy path the same way and is
    compensated in one place. The workflow completes carrying an unsuccessful
    result rather than failing, which is how a declined order stays
    distinguishable from a broken one.
    """


# Dapr Workflow Definition for Order Processing

def process_order_workflow(ctx: wf.DaprWorkflowContext, order: Order):
    # Compensations for the steps taken so far, unwound in reverse by the one
    # handler below. Each is registered *before* the step it undoes, because a
    # failed call's outcome is ambiguous: the server may have committed before
    # the response was lost. Everything on this stack must therefore be
    # best-effort and tolerate having nothing to undo.
    compensations = []

    try:
        custom_status = {
            "order": order.id,
            "customer": order.customer,
        }
        ctx.set_custom_status(json.dumps(custom_status))

        yield ctx.call_activity(notify, input=f"Processing order for {order.customer}. Item: {order.item}, Qty: {order.qty}")

        # If the order didn't come with an explicit total, price it from the
        # inventory catalog (unit price * quantity).
        if not order.total:
            order.total = yield ctx.call_activity(price_order, input=order)
            yield ctx.call_activity(notify, input=f"Priced order at {order.total} ({order.item} x{order.qty})")

        # Reserve the items. The reservation is provisional: it moves stock but
        # records no sale until confirm_inventory runs at the end.
        compensations.append(release_inventory)
        result = yield ctx.call_activity(reserve_inventory, input=order)
        if not result.success:
            raise OrderFailed(result.message)

        yield ctx.call_activity(notify, input=f"Reserved inventory: {order.item} x{order.qty}")

        # Orders over $1,000 require human approval
        if order.total >= APPROVAL_THRESHOLD:
            approval_deadline = ctx.current_utc_datetime + APPROVAL_TIMEOUT
            yield ctx.call_activity(notify, input=f"Waiting for approval since order >= {APPROVAL_THRESHOLD}. Deadline = {approval_deadline}.")

            # Block the workflow on either an approval event or a timeout
            approval_task = ctx.wait_for_external_event("approval")
            timeout_expired_task = ctx.create_timer(approval_deadline)
            winner = yield wf.when_any([approval_task, timeout_expired_task])

            if winner == timeout_expired_task:
                raise OrderFailed("Approval deadline expired.")

            # Check the approval result
            approval: Approval = yield approval_task
            if not approval.approved:
                raise OrderFailed(f"Order was rejected by {approval.approver}.")

            yield ctx.call_activity(notify, input=f"Order was approved by {approval.approver}.")

        yield ctx.call_activity(notify, input="Attempting to take payment")

        # Take the money. The payments service owns the charge, so this runs as
        # a child workflow there; a declined card comes back as an unsuccessful
        # result rather than an error.
        compensations.append(refund_payment)
        result = yield ctx.call_child_workflow(
            PAYMENT_WORKFLOW,
            input={
                "order_id": order.id,
                "customer": order.customer,
                "total": order.total,
            },
            instance_id=f"payment_{order.id}",
            app_id=PAYMENTS_APP_ID,
        )
        if not result.success:
            raise OrderFailed(result.message)

        yield ctx.call_activity(notify, input="Payment processed")

        yield ctx.call_activity(notify, input="Order submitted for shipping")

        yield ctx.call_activity(submit_order_to_shipping, input=order)

        yield ctx.call_activity(notify, input="Shipment scheduled")

        # Turn the reservation into a sale. The order has already shipped, so a
        # failure here must not unwind the compensations: report it and let the
        # order stand. The cost is a missing sales-history entry, which only
        # degrades the demand forecast.
        try:
            confirmation = yield ctx.call_activity(confirm_inventory, input=order)
            if not confirmation.success:
                yield ctx.call_activity(notify, input=f"Could not confirm reservation: {confirmation.message}")
        except Exception as e:
            yield ctx.call_activity(notify, input=f"Could not confirm reservation: {e}")

        # Fulfil the order. The shipping service owns getting the goods out, so
        # this runs as a child workflow there: prepare, then dispatch. A failure
        # in either unwinds the compensations like any other step, putting the
        # goods back on the shelf and refunding the customer.
        compensations.append(return_inventory)
        yield ctx.call_child_workflow(
            DELIVERY_WORKFLOW,
            input={
                "order_id": order.id,
                "customer": order.customer,
                "item": order.item,
                "qty": order.qty,
            },
            instance_id=f"delivery_{order.id}",
            app_id=SHIPPING_APP_ID,
        )

        yield ctx.call_activity(notify, input=f"Order processed for {order.customer}. Item: {order.item}, Qty: {order.qty}, Total: {order.total}")

        return OrderResult(order.id, True, "Order processed")

    except Exception as e:
        for compensation in reversed(compensations):
            yield ctx.call_activity(compensation, input=order)
        yield ctx.call_activity(notify, input=f"Order failed: {e}")

        if isinstance(e, OrderFailed):
            return OrderResult(order.id, False, str(e))
        raise

def notify(ctx: wf.WorkflowActivityContext, message: str):
    """Report progress against the order this workflow is processing."""
    logging.info(f"Sending notification: {message}")
    notifications.publish(ctx.workflow_id, message)


def price_order(_, order: Order) -> float:
    """Look up the unit price for `order.item` and return price * qty."""
    order = to_order(order)
    logging.info(f"Pricing order from inventory catalog: {order}")
    with DaprClient() as d:
        resp = d.invoke_method("inventory", "api/v1/inventory", http_verb="GET")
        if resp.status_code != 200:
            raise Exception(f"Error calling inventory service: {resp.status_code}")
        inventory = json.loads(resp.data.decode("utf-8"))
    for entry in inventory:
        if entry.get("name", "").lower() == order.item.lower():
            total = float(entry["price"]) * order.qty
            logging.info(f"Priced {order.item} x{order.qty} at {total}")
            return total
    raise Exception(f"Item {order.item} not found in inventory catalog")


def reserve_inventory(_, order: Order) -> InventoryResult:
    order = to_order(order)
    logging.info(f"Reserving inventory for order: {order}")
    with DaprClient() as d:
        resp = d.invoke_method("inventory", "api/v1/inventory/reserve", http_verb="POST", data=json.dumps(asdict(order)))
        if resp.status_code != 200:
            raise Exception(f"Error calling inventory service: {resp.status_code}")
        inventory_result = InventoryResult(**json.loads(resp.data.decode("utf-8")))
        logging.info(f"Inventory result: {inventory_result}")
        return inventory_result


def confirm_inventory(_, order: Order) -> InventoryResult:
    """Turn the order's reservation into a sale.

    Runs once the order has shipped; until it does, the reserved stock carries
    no sales record.
    """
    order = to_order(order)
    logging.info(f"Confirming inventory reservation for order: {order}")
    with DaprClient() as d:
        resp = d.invoke_method(
            "inventory", "api/v1/inventory/confirm",
            http_verb="POST", data=json.dumps(asdict(order)),
        )
        if resp.status_code != 200:
            raise Exception(f"Error calling inventory service: {resp.status_code}")
        inventory_result = InventoryResult(**json.loads(resp.data.decode("utf-8")))
        logging.info(f"Confirmation result: {inventory_result}")
        return inventory_result


def return_inventory(_, order: Order):
    """Best-effort compensation for a delivery that never arrived.

    Puts a sold order's stock back on the shelf. Nothing happens if the order
    was never sold — `release_inventory` covers that case — so both can sit on
    the compensation list and exactly one of them credits the stock. Errors are
    logged, not raised, so compensation never masks the original failure.
    """
    order = to_order(order)
    logging.info(f"Returning inventory for order: {order}")
    try:
        with DaprClient() as d:
            resp = d.invoke_method(
                "inventory", "api/v1/inventory/return",
                http_verb="POST", data=json.dumps(asdict(order)),
            )
            if resp.status_code != 200:
                logging.warning(
                    f"Inventory return returned {resp.status_code} for {order.id}"
                )
    except Exception as e:
        logging.warning(f"Inventory return call failed for {order.id}: {e}")


def release_inventory(_, order: Order):
    """Best-effort compensation for `reserve_inventory`.

    Puts the reserved stock back. The reservation may never have committed, in
    which case the inventory service treats this as a no-op. Errors are logged,
    not raised, so compensation never masks the original failure.
    """
    order = to_order(order)
    logging.info(f"Releasing inventory for order: {order}")
    try:
        with DaprClient() as d:
            resp = d.invoke_method(
                "inventory", "api/v1/inventory/release",
                http_verb="POST", data=json.dumps(asdict(order)),
            )
            if resp.status_code != 200:
                logging.warning(
                    f"Inventory release returned {resp.status_code} for {order.id}"
                )
    except Exception as e:
        logging.warning(f"Inventory release call failed for {order.id}: {e}")

def submit_order_to_shipping(_, order: Order):
    order = to_order(order)
    logging.info(f"Submitting order to shipping: {order}")
    with DaprClient() as d:
        resp = d.invoke_method(
            "shipping", "shipping/ship",
            http_verb="POST", data=json.dumps(asdict(order)),
        )
        if resp.status_code != 200:
            raise Exception(f"Error calling shipping service: {resp.status_code}: {resp.text()}")

def refund_payment(_, order: Order):
    """Best-effort compensation for the payment workflow.

    The charge may never have been taken, and errors are logged rather than
    raised: a failure here must not stop the compensations behind it from
    running, nor mask the failure that triggered the unwind.
    """
    order = to_order(order)
    logging.info(f"Refunding payment for order: {order}")
    try:
        with DaprClient() as d:
            resp = d.invoke_method(
                "payments", f"api/v1/payments/{order.id}/refunds",
                http_verb="POST", data=json.dumps(asdict(order)),
            )
            if resp.status_code != 200:
                logging.warning(
                    f"Payment refund returned {resp.status_code} for {order.id}"
                )
    except Exception as e:
        logging.warning(f"Payment refund call failed for {order.id}: {e}")


def register(wf_runtime: wf.WorkflowRuntime) -> None:
    """Register the order workflow and its activities on a runtime."""
    wf_runtime.register_workflow(process_order_workflow)
    wf_runtime.register_activity(notify)
    wf_runtime.register_activity(price_order)
    wf_runtime.register_activity(reserve_inventory)
    wf_runtime.register_activity(confirm_inventory)
    wf_runtime.register_activity(return_inventory)
    wf_runtime.register_activity(release_inventory)
    wf_runtime.register_activity(submit_order_to_shipping)
    wf_runtime.register_activity(refund_payment)
