"""Delivery workflow.

Gets a paid order out of the warehouse: the shipment is prepared, then
dispatched.

The order-processor runs this as a child workflow across app boundaries, by
name and with `app_id="shipping"`, so a failure here surfaces to the order's
compensation handler.
"""

import json
import logging
from dataclasses import asdict

import dapr.ext.workflow as wf
from dapr.clients import DaprClient

import notifications
from models import Delivery, DeliveryResult, to_delivery

logger = logging.getLogger("shipping.delivery")


def delivery_workflow(ctx: wf.DaprWorkflowContext, delivery: Delivery):
    """Prepare the shipment, then dispatch it."""
    delivery = to_delivery(delivery)

    def announce(message: str):
        return ctx.call_activity(
            notify, input={"order_id": delivery.order_id, "message": message}
        )

    yield ctx.call_activity(prepare_shipment, input=delivery)
    yield announce(f"Shipment prepared for {delivery.item} x{delivery.qty}")

    yield ctx.call_activity(dispatch_shipment, input=delivery)
    yield announce(f"Shipment dispatched to {delivery.customer}")

    return DeliveryResult(delivery.order_id, True, "Shipment dispatched")


def prepare_shipment(_: wf.WorkflowActivityContext, delivery: Delivery):
    delivery = to_delivery(delivery)
    logger.info(f"Preparing shipment: {delivery}")
    with DaprClient() as d:
        resp = d.invoke_method("shipping", "shipping/prepare", http_verb="POST", data=json.dumps(asdict(delivery)))
        if resp.status_code != 200:
            raise Exception(f"Error calling shipping service: {resp.status_code}: {resp.text()}")


def dispatch_shipment(_: wf.WorkflowActivityContext, delivery: Delivery):
    delivery = to_delivery(delivery)
    logger.info(f"Dispatching shipment: {delivery}")
    with DaprClient() as d:
        resp = d.invoke_method("shipping", "shipping/dispatch", http_verb="POST", data=json.dumps(asdict(delivery)))
        if resp.status_code != 200:
            raise Exception(f"Error calling shipping service: {resp.status_code}: {resp.text()}")


def notify(_: wf.WorkflowActivityContext, payload):
    """Publish a progress message under the order it belongs to.

    The order id is passed explicitly rather than taken from the workflow
    context so that the delivery's messages join the order's feed in the
    notifications UI instead of forming one of their own.
    """
    fields = payload if isinstance(payload, dict) else vars(payload)
    order_id, message = fields["order_id"], fields["message"]
    logger.info(f"Sending notification: {message}")
    notifications.publish(order_id, message)


def register(wf_runtime: wf.WorkflowRuntime) -> None:
    """Register the delivery workflow and its activities on a runtime."""
    wf_runtime.register_workflow(delivery_workflow)
    wf_runtime.register_activity(prepare_shipment)
    wf_runtime.register_activity(dispatch_shipment)
    wf_runtime.register_activity(notify)
