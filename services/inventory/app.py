from typing import List, Dict
import logging
import os
import grpc
import json

from pydantic import BaseModel
from dapr.clients import DaprClient
from dapr.clients.grpc._state import StateItem
from flask import Flask, g, request, jsonify

APP_PORT = os.getenv("APP_PORT", "3002")
STATESTORE_NAME = os.getenv("STATESTORE_NAME", "inventory")

app = Flask(__name__)

logger = logging.getLogger("inventory_controller")


class InventoryItem:
    def __init__(self, item: str, quantity: int):
        self.item = item
        self.quantity = quantity

    # Method to convert the object to a dictionary
    def to_dict(self):
        return {"name": self.item, "quantity": self.quantity}


item_keys = ["orange", "apple", "pear", "kiwi"]


@app.get("/inventory")
def get_inventory():
    inventory = []

    with DaprClient() as d:
        try:
            for item_key in item_keys:

                item = d.get_state(STATESTORE_NAME, item_key.lower())

                if item.data:
                    value = item.data.decode('utf-8')
                    inventory.append(InventoryItem(item=item_key, quantity=value).to_dict())

            if len(inventory) == 0:
                return {"message": "No Inventory available."}

            else:
                return inventory

        except grpc.RpcError as err:
            logging.info('Error occurred while retrieving state: %s. Exception= %s' %
                         (str(item_key), {err.details()}))
            return "Retrieving inventory was unsuccessful", 500


@app.route("/inventory/restock", methods=["POST"])
def restock_inventory():

    with DaprClient() as d:
        for item_key in item_keys:
            d.save_state(STATESTORE_NAME, item_key, "100")

    logger.info("Inventory Restocked!")
    return {"message": "Inventory has been restocked."}


@app.delete("/inventory")
def clear_inventory():
    with DaprClient() as d:
        try:
            for item_key in item_keys:
                d.delete_state(STATESTORE_NAME, item_key.lower())

            logger.info("Cleared inventory!")

        except grpc.RpcError as err:
            logging.info('Error occurred while deleting state: %s. Exception= %s' %
                         (str(item_key), {err.details()}))
            return "Deleting inventory was unsuccessful", 500

    return {"message": "Inventory has been cleared."}


@app.post("/inventory/reserve")
def reserve_inventory():
    logging.info(f"Reserving inventory: {request.json}")

    order = request.json
    items = order['items']
    order_id = order['id']

    # Check if we have enough inventory to fulfill the order
    with DaprClient() as d:
        for item in items:
            match = d.get_state(STATESTORE_NAME, item.lower())
            quantity = int.from_bytes(match.data)

            if (not match.data) or (quantity <= 0):
                return {
                    "id": order_id,
                    "success": False,
                    "message": "Out of stock",
                }, 200

            return {
                "id": order_id,
                "success": True,
                "message": "Items reserved successfully",
            }, 200


@ app.route("/", methods=["GET"])
@ app.route("/healthz", methods=["GET"])
def hello():
    return f"Hello from {__name__}", 200


def main():
    # Start the Flask app server
    app.run(port=int(APP_PORT), debug=True, use_reloader=False)


if __name__ == "__main__":
    logging.basicConfig(
        format='%(asctime)s.%(msecs)03d %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)
    main()
