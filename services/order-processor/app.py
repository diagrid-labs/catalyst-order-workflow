import json
import logging
import os
import random
import string
import dapr.ext.workflow as wf
from dapr.clients import DaprClient
from flask import Flask, request, url_for
from markupsafe import escape
from dataclasses import dataclass, asdict
from datetime import timedelta

APP_PORT = os.getenv("APP_PORT", "3006")
PUBSUB_NAME = os.getenv("PUBSUB_NAME", "pubsub")
TOPIC_NAME = os.getenv("TOPIC_NAME", "notifications")

APPROVAL_THRESHOLD = 1000.0
APPROVAL_TIMEOUT = timedelta(hours=24)

app = Flask(__name__)


@dataclass
class Order:
    id: str
    customer: str
    item: str
    total: float = 0.0
    qty: int = 1

    def __post_init__(self):
        if not self.id:
            raise ValueError("id required")
        if not self.customer:
            raise ValueError("customer required")
        if not self.item:
            raise ValueError("item required")
        if self.qty < 1:
            raise ValueError("qty must be >= 1")
        if self.total < 0:
            raise ValueError("total must be >= 0")

    @classmethod
    def new(cls, *, customer: str, item: str, total: float = 0.0, qty: int = 1) -> "Order":
        """Build a new Order with a freshly generated id.

        Reconstructing an existing order (e.g. from workflow state) should use
        the regular constructor with `id` supplied.
        """
        if not customer:
            raise ValueError("customer required")
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=5))
        return cls(
            id=f"order_{customer.lower()}_{suffix}",
            customer=customer,
            item=item,
            total=total,
            qty=qty,
        )

@dataclass
class Approval:
    approver: str
    approved: bool

    def __post_init__(self):
        if not self.approver:
            raise ValueError("approver required")
        if not isinstance(self.approved, bool):
            raise ValueError("approved must be a boolean")

@dataclass
class OrderResult:
    id: str
    success: bool
    message: str

@dataclass
class InventoryResult:
    id: str
    success: bool
    message: str

@dataclass
class PaymentResult:
    id: str
    success: bool
    message: str


def _to_order(value) -> Order:
    """Reconstruct an Order at an activity boundary.

    The Dapr workflow SDK serializes dataclass inputs to JSON and
    deserializes them back as types.SimpleNamespace, so the `order: Order`
    annotation on activity functions is a hint, not a runtime guarantee.
    Rebuilding the dataclass here makes the hint honest and lets us use
    asdict() consistently for re-serialization.
    """
    return value if isinstance(value, Order) else Order(**vars(value))


# Dapr Workflow Definition for Order Processing

def process_order_workflow(ctx: wf.DaprWorkflowContext, order: Order):
    yield ctx.call_activity(notify, input=f"Processing order for {order.customer}. Item: {order.item}, Qty: {order.qty}")

    # If the order didn't come with an explicit total, price it from the
    # inventory catalog (unit price * quantity).
    if not order.total:
        order.total = yield ctx.call_activity(price_order, input=order)
        yield ctx.call_activity(notify, input=f"Priced order at {order.total} ({order.item} x{order.qty})")

    # Call into the inventory service to reserve the items in this order
    result = yield ctx.call_activity(reserve_inventory, input=order)

    if not result.success:
        yield ctx.call_activity(notify, input=f"Failed to reserve inventory: {result.message}")
        return OrderResult(order.id, False, result.message)

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
            message = "Approval deadline expired."
            yield ctx.call_activity(notify, input=message)
            return OrderResult(order.id, False, message)

        # Check the approval result
        approval: Approval = yield approval_task
        if not approval.approved:
            message = f"Order was rejected by {approval.approver}."
            yield ctx.call_activity(notify, input=message)
            return OrderResult(order.id, False, message)

        yield ctx.call_activity(notify, input=f"Order was approved by {approval.approver}.")

    yield ctx.call_activity(notify, input="Attempting to take payment")

    # Submit the order to the payment service
    try:
        result = yield ctx.call_activity(submit_payment, input=order)
        if not result.success:
            yield ctx.call_activity(notify, input=f"Payment failed for order: {result.message}")
            return OrderResult(order.id, False, result.message)
    except Exception as e:
        yield ctx.call_activity(notify, input=f"Error taking payment: {str(e)}")
        raise

    yield ctx.call_activity(notify, input="Payment processed")

    yield ctx.call_activity(notify, input="Order submitted for shipping")

    # Submit the order for shipping
    try:
        yield ctx.call_activity(submit_order_to_shipping, input=order)
    except Exception as e:
        # Shipping failed, so we need to refund the payment
        yield ctx.call_activity(notify, input=f"Error submitting order for shipping: {str(e)}")
        yield ctx.call_activity(refund_payment, input=order)
        yield ctx.call_activity(notify, input="Payment refunded")

        # Allow the workflow to fail with the original failure details
        raise

    yield ctx.call_activity(notify, input="Shipment scheduled")

    yield ctx.call_activity(notify, input=f"Order processed for {order.customer}. Item: {order.item}, Qty: {order.qty}, Total: {order.total}")

    return OrderResult(order.id, True, "Order processed")

def notify(ctx: wf.WorkflowActivityContext, message: str):
    logging.info(f"Sending notification: {message}")
    with DaprClient() as d:
        d.publish_event(PUBSUB_NAME, TOPIC_NAME, json.dumps({
            "order_id": ctx.workflow_id,
            "message": message,
            "data-content-type": "application/json"
        }))


def price_order(_, order: Order) -> float:
    """Look up the unit price for `order.item` and return price * qty."""
    order = _to_order(order)
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
    order = _to_order(order)
    logging.info(f"Reserving inventory for order: {order}")
    with DaprClient() as d:
        resp = d.invoke_method("inventory", "api/v1/inventory/reserve", http_verb="POST", data=json.dumps(asdict(order)))
        if resp.status_code != 200:
            raise Exception(f"Error calling inventory service: {resp.status_code}")
        inventory_result = InventoryResult(**json.loads(resp.data.decode("utf-8")))
        logging.info(f"Inventory result: {inventory_result}")
        return inventory_result

def submit_order_to_shipping(_, order: Order):
    order = _to_order(order)
    logging.info(f"Submitting order to shipping: {order}")
    with DaprClient() as d:
        resp = d.invoke_method("shipping", "shipping/ship", http_verb="POST", data=json.dumps(asdict(order)))
        if resp.status_code != 200:
            raise Exception(f"Error calling shipping service: {resp.status_code}: {resp.text()}")

def submit_payment(_, order: Order) -> PaymentResult:
    order = _to_order(order)
    logging.info(f"Submitting payment for order: {order}")
    with DaprClient() as d:
        resp = d.invoke_method("payments", "api/v1/payments", http_verb="POST", data=json.dumps(asdict(order)))
        payment_result = PaymentResult(**json.loads(resp.data.decode("utf-8")))

        if resp.status_code != 201:
            if 'declined' not in payment_result.message:
                raise Exception(f"Error calling payment service: {resp.status_code}: {resp.text()}")

        logging.info(f"Payment result: {payment_result}")
        return payment_result

def refund_payment(_, order: Order):
    order = _to_order(order)
    logging.info(f"Refunding payment for order: {order}")
    with DaprClient() as d:
        resp = d.invoke_method("payments", f"api/v1/payments/{order.id}/refunds", http_verb="POST", data=json.dumps(asdict(order)))
        if resp.status_code != 200:
            raise Exception(f"Error calling payment service: {resp.status_code}: {resp.text()}")


# API to submit a new order
@app.route("/orders", methods=["POST"])
def submit_order():
    request_data = request.get_json()
    if not request_data:
        return """Invalid request. Should be in the form of {
            \"customer\": \"joe\", \"item\": \"apples\", \"qty\": 3}""", 400

    # `total` is optional. If not provided, the workflow prices the order
    # from the inventory catalog (unit price * qty).
    try:
        order = Order.new(
            customer=request_data.get("customer"),
            item=request_data.get("item"),
            total=float(request_data.get("total") or 0),
            qty=int(request_data.get("qty", 1)),
        )
    except (TypeError, ValueError) as e:
        return f"Invalid order: {e}", 400

    wf_client = wf.DaprWorkflowClient()
    instance_id = wf_client.schedule_new_workflow(
        process_order_workflow,
        input=order,
        instance_id=order.id)

    logging.info(f"Started workflow instance: {instance_id}")

    return json.dumps({"instance_id": instance_id}), 202, {
        'Content-Type': 'application/json',
        'Location': url_for('check_order_status', order_id=instance_id, _external=True)
    }


@app.route("/orders/<order_id>", methods=["GET"])
def check_order_status(order_id):
    wf_client = wf.DaprWorkflowClient()
    state = wf_client.get_workflow_state(order_id)
    if not state:
        return f"Order not found: {escape(order_id)}", 404

    order = Order(**json.loads(state.serialized_input))
    resp = {
        "id": state.instance_id,
        "details": asdict(order),
        "status": state.runtime_status.name,
        "created_time": state.created_at.isoformat(),
        "last_updated_time": state.last_updated_at.isoformat(),
    }

    if state.serialized_output:
        order_result = OrderResult(**json.loads(state.serialized_output))
        resp["order_result"] = asdict(order_result)

    if state.failure_details:
        resp["failure_details"] = {
            "message": state.failure_details.message,
            "error_type": state.failure_details.error_type,
            "stack_trace": state.failure_details.stack_trace
        }

    return resp, 200


@app.route("/orders/<order_id>/approve", methods=["POST"])
def approve_order(order_id):
    request_data = request.get_json()
    if not request_data:
        return """Invalid request. Should be in the form of { \"approver\": \"joe\", \"approved\": true }""", 400

    try:
        approval = Approval(
            approver=request_data.get("approver"),
            approved=request_data.get("approved"),
        )
    except (TypeError, ValueError) as e:
        return f"Invalid approval: {e}", 400

    wf_client = wf.DaprWorkflowClient()
    wf_client.raise_workflow_event(order_id, "approval", data=approval)

    return f"Approval sent for order: {escape(order_id)}", 200


@app.route("/", methods=["GET"])
@app.route("/healthz", methods=["GET"])
def hello():
    return f"Hello from {__name__}", 200


def main():
    # Start the workflow runtime
    logging.info("Starting workflow runtime...")
    wf_runtime = wf.WorkflowRuntime()  # host/port comes from env vars
    wf_runtime.register_workflow(process_order_workflow)
    wf_runtime.register_activity(notify)
    wf_runtime.register_activity(price_order)
    wf_runtime.register_activity(reserve_inventory)
    wf_runtime.register_activity(submit_payment)
    wf_runtime.register_activity(submit_order_to_shipping)
    wf_runtime.register_activity(refund_payment)
    wf_runtime.start()  # non-blocking

    # Start the Flask app server
    app.run(host='0.0.0.0', port=APP_PORT, debug=False, use_reloader=False)

    # Stop the workflow runtime to allow the process to terminate
    wf_runtime.shutdown()


if __name__ == "__main__":
    logging.basicConfig(
        format='%(asctime)s.%(msecs)03d %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)
    main()
