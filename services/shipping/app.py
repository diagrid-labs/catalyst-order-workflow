import logging
import os

import dapr.ext.workflow as wf
from flask import Flask, jsonify, request

import delivery

APP_PORT = os.getenv("APP_PORT", "3004")

app = Flask(__name__)

is_deactivated = False

DEACTIVATED_MESSAGE = "The shipping service is currently deactivated for routine maintenance."


@app.route("/shipping/ship", methods=["POST"])
def ship():
    if is_deactivated:
        return DEACTIVATED_MESSAGE, 503
    logging.info(f"Shipping order: {request.json}")
    return '', 200


@app.route("/shipping/prepare", methods=["POST"])
def prepare():
    if is_deactivated:
        return DEACTIVATED_MESSAGE, 503
    logging.info(f"Preparing shipment: {request.json}")
    return '', 200


@app.route("/shipping/dispatch", methods=["POST"])
def dispatch():
    if is_deactivated:
        return DEACTIVATED_MESSAGE, 503
    logging.info(f"Dispatching shipment: {request.json}")
    return '', 200


@app.route("/shipping/deliver", methods=["POST"])
def deliver():
    if is_deactivated:
        return DEACTIVATED_MESSAGE, 503
    logging.info(f"Delivering shipment: {request.json}")
    return '', 200


@app.route("/shipping/deactivate", methods=["POST"])
def deactivate():
    global is_deactivated
    is_deactivated = True
    logging.warning("The shipping service has been deactivated for routine maintenance.")
    return '', 200


@app.route("/shipping/activate", methods=["POST"])
def activate():
    global is_deactivated
    is_deactivated = False
    logging.info("The shipping service has been (re)activated.")
    return '', 200


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
        "service": "shipping",
        "status": "healthy",
    })


def main():
    logging.info("Starting delivery workflow runtime...")
    wf_runtime = wf.WorkflowRuntime()
    delivery.register(wf_runtime)
    wf_runtime.start()

    app.run(host='0.0.0.0', port=APP_PORT, debug=False, use_reloader=False)

    wf_runtime.shutdown()


if __name__ == "__main__":
    logging.basicConfig(
        format='%(asctime)s.%(msecs)03d %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)
    main()
