"""REST API for orders.

Starts an order workflow, reports on one, and delivers the approval a large
order waits for. The workflow itself lives in `order.py`.
"""

import json
import logging
import os
from dataclasses import asdict

import dapr.ext.workflow as wf
from flask import Flask, jsonify, request, url_for
from markupsafe import escape

import order as order_workflow
from models import Approval, Order, OrderResult

APP_PORT = os.getenv("APP_PORT", "3006")

app = Flask(__name__)


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
        order_workflow.process_order_workflow,
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
def root():
    """Answer Catalyst's app-endpoint validation probe.

    Catalyst probes the root path of the app endpoint every few seconds,
    marking the request with an `X-Diagridio-Validation` header. Health
    reporting is `/health`; this exists so that probe gets a 200.
    """
    return '', 200


@app.route("/health", methods=["GET"])
@app.route("/healthz", methods=["GET"])
def health_check():
    return jsonify({
        "service": "order-processor",
        "status": "healthy",
    })


def main():
    # Start the workflow runtime
    logging.info("Starting workflow runtime...")
    wf_runtime = wf.WorkflowRuntime()  # host/port comes from env vars
    order_workflow.register(wf_runtime)
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
