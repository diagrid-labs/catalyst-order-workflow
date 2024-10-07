import logging
import os
import time
import json
import random
import uuid
import grpc

from dapr.clients import DaprClient
from flask import Flask, request
from models import AppFeeMoney, AmountMoney, CreatePayment, Order, PaymentResult

APP_PORT = os.getenv("APP_PORT", "3003")

app = Flask(__name__)


@app.post("/payments/charge")
def charge():

    order_data = request.get_json()

    # Create an Order instance
    order = Order(**order_data)  # Unpack the dictionary to create an instance

    logging.info(f"Charging payment for order: {order}")

    # Randomly choosing a test card, one which fails and one which succeeds
    sourceIds = list({"cnon:card-nonce-ok", "4000000000000002"})

    # try:
    # Define the custom content type
    metadata = {
        'Content-Type': 'application/json',
        'Square-Version': '2024-09-19',
        # 'Authorization': f'Bearer {square_api_key}',
        'path': '/v2/payments'
    }

    # Define the amount money object
    cost = AmountMoney(amount=order.total, currency="USD")

    # Generate a unique idempotency key
    idempotency_key = str(uuid.uuid4())

    # Create the instance of CreatePayment
    payment_instance = CreatePayment(
        amount_money=cost,
        idempotency_key=idempotency_key,
        source_id=random.choice(sourceIds),
        autocomplete=True,
        customer_id=order.customer,
        reference_id=(order.customer + str(idempotency_key)),
        note="Payment attempt",
        # app_fee_money=app_fee_money  # Optional; you can skip this if not needed
    )

    payment_data = payment_instance.dict()

    # Initialize the Dapr client
    with DaprClient() as d:
        try:
            # Invoke the binding with the custom content type and payload
            response = d.invoke_binding(
                binding_name='square-api',
                operation='create',
                data=json.dumps(payment_data).encode('utf-8'),
                binding_metadata=metadata
            )

        except grpc.RpcError as err:
            logging.info(f"Error={err.details()}")

            if '400' in err.details():
                payment_result = {"success": False, "message": "Payment was declined"}
                logging.info("Payment was declined by Square API")
                return payment_result, 200
            else:
                logging.info("Error invoking Square API")
                return '', 500

    logging.info("Payment successful")
    logging.info(response.data)
    payment_result = {"success": True, "message": "Payment was accepted"}
    return payment_result, 200


@app.route("/payments/refund", methods=["POST"])
def refund():
    logging.info(f"Refunding payment for order: {request.json}")

    # Simulate work
    time.sleep(1)

    return '', 200


@app.route("/", methods=["GET"])
@app.route("/healthz", methods=["GET"])
def hello():
    return f"Hello from {__name__}", 200


def main():
    # Start the Flask app server
    app.run(port=APP_PORT, debug=True, use_reloader=False)


if __name__ == "__main__":
    logging.basicConfig(
        format='%(asctime)s.%(msecs)03d %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)
    main()
