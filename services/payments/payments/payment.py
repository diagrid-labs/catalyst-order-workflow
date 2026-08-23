"""Payment workflow.

Takes the money for an order by charging the card.

The order-processor runs this as a child workflow across app boundaries, by name
and with `app_id="payments"`, so a declined card comes back as an unsuccessful
result and anything else surfaces to the order's compensation handler.
"""

import json
import logging
from dataclasses import asdict

import dapr.ext.workflow as wf
from dapr.clients import DaprClient

import notifications
from payments.models import Payment, PaymentResult, to_payment

logger = logging.getLogger("payments.payment")


def payment_workflow(ctx: wf.DaprWorkflowContext, payment: Payment):
    """Charge the customer, and report whether the charge went through."""
    payment = to_payment(payment)

    def announce(message: str):
        return ctx.call_activity(
            notify, input={"order_id": payment.order_id, "message": message}
        )

    result = yield ctx.call_activity(charge_card, input=payment)

    if not result.success:
        yield announce(f"Payment declined: {result.message}")
        return PaymentResult(payment.order_id, False, result.message)

    yield announce(f"Charged {payment.customer} {payment.total}")

    return PaymentResult(payment.order_id, True, "Payment processed")


def charge_card(_: wf.WorkflowActivityContext, payment: Payment) -> PaymentResult:
    """Charge the card, distinguishing a declined card from a broken service.

    A decline is an answer, so it comes back as an unsuccessful result and the
    order fails cleanly. Any other non-201 is a fault and raises.
    """
    payment = to_payment(payment)
    logger.info(f"Charging card for payment: {payment}")
    with DaprClient() as d:
        resp = d.invoke_method("payments", "api/v1/payments", http_verb="POST", data=json.dumps(asdict(payment)))
        charge = json.loads(resp.data.decode("utf-8"))
        if resp.status_code != 201:
            if 'declined' not in charge.get("message", ""):
                raise Exception(f"Error calling payment service: {resp.status_code}: {resp.text()}")
            return PaymentResult(payment.order_id, False, charge["message"])

    logger.info(f"Charge result: {charge}")
    return PaymentResult(payment.order_id, True, charge.get("message", "Payment processed"))


def notify(_: wf.WorkflowActivityContext, payload):
    """Publish a progress message under the order it belongs to.

    The order id is passed explicitly rather than taken from the workflow
    context so that the payment's messages join the order's feed in the
    notifications UI instead of forming one of their own.
    """
    fields = payload if isinstance(payload, dict) else vars(payload)
    order_id, message = fields["order_id"], fields["message"]
    logger.info(f"Sending notification: {message}")
    notifications.publish(order_id, message)


def register(wf_runtime: wf.WorkflowRuntime) -> None:
    """Register the payment workflow and its activities on a runtime."""
    wf_runtime.register_workflow(payment_workflow)
    wf_runtime.register_activity(charge_card)
    wf_runtime.register_activity(notify)
