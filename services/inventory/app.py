from typing import List
import logging
import os
import grpc
from dapr.clients import DaprClient
from dapr.clients.grpc._state import StateItem
from flask import Flask, request, jsonify, make_response

APP_PORT = int(os.getenv("APP_PORT", 3002))
STATESTORE_NAME = os.getenv("STATESTORE_NAME", "statestore")

app = Flask(__name__)

logger = logging.getLogger("inventory_service")

INVENTORY_ITEMS = ["orange", "apple", "pear", "kiwi"]


class InventoryItem:
    def __init__(self, item: str, quantity: int):
        self.item = item
        self.quantity = quantity

    def to_dict(self):
        return {"name": self.item, "quantity": self.quantity}


@app.route('/api/v1/inventory', methods=['GET'])
def get_inventory():

    inventory = []

    with DaprClient() as d:
        try:
            for item_key in INVENTORY_ITEMS:
                item = d.get_state(STATESTORE_NAME, item_key.lower())

                if item.data:
                    value = item.data.decode('utf-8')
                    inventory.append(InventoryItem(item=item_key, quantity=value).to_dict())

            if not inventory:
                return jsonify({"message": "No inventory available."})
            
            return jsonify(inventory)

        except grpc.RpcError as err:
            logger.error(f'Error retrieving inventory: {err.details()}')
            return make_response(
                jsonify({"error": "Internal Server Error", "message": "Failed to retrieve inventory"}),
                500
            )


@app.route('/api/v1/inventory', methods=['DELETE'])
def clear_inventory():
    with DaprClient() as d:
        try:
            for item_key in INVENTORY_ITEMS:
                d.delete_state(STATESTORE_NAME, item_key.lower())

            logger.info("Inventory cleared successfully")
            return jsonify({"message": "Inventory has been cleared."})

        except grpc.RpcError as err:
            logger.error(f'Error clearing inventory: {err.details()}')
            return make_response(
                jsonify({"error": "Internal Server Error", "message": "Failed to clear inventory"}),
                500
            )


@app.route('/api/v1/inventory/restock', methods=['POST'])
def restock_inventory():
    try:
        with DaprClient() as d:
            state_items = []
            
            # Create a StateItem for each inventory item
            for item_key in INVENTORY_ITEMS:
                state_item = StateItem(
                    key=item_key,
                    value="100",  # Default quantity
                    etag="",  # No etag since we're just overwriting
                    metadata={},
                    options=None
                )
                state_items.append(state_item)
            
            # Save all items in a single operation
            d.save_bulk_state(store_name=STATESTORE_NAME, states=state_items)

        logger.info("Inventory restocked successfully")
        return jsonify({"message": "Inventory has been restocked."})
    
    except grpc.RpcError as err:
        logger.error(f'Error restocking inventory: {err.details()}')
        return make_response(
            jsonify({"error": "Internal Server Error", "message": "Failed to restock inventory"}),
            500
        )


@app.route('/api/v1/inventory/reserve', methods=['POST'])
def reserve_inventory():
    if not request.is_json:
        return make_response(
            jsonify({"error": "Bad Request", "message": "Request must be JSON"}),
            400
        )
    
    order = request.json
    if not order or 'item' not in order or 'id' not in order:
        return make_response(
            jsonify({"error": "Bad Request", "message": "Invalid order format"}),
            400
        )
    
    item = order['item']
    order_id = order['id']
    
    logger.info(f"Processing inventory reservation for order {order_id}: {item}")

    # Check if we have enough inventory to fulfill the order
    try:
        with DaprClient() as d:
            
            # Get the state for this item
            state_response = d.get_state(STATESTORE_NAME, item.lower())
            
            # Check if we got any data back
            if not state_response.data:
                return jsonify({
                    "id": order_id,
                    "success": False,
                    "message": f"Item {item} not found in inventory"
                })
            
            # Safely decode and convert the quantity
            try:
                quantity = int(state_response.data.decode('utf-8'))
            except (AttributeError, ValueError) as e:
                logger.error(f"Error processing item {item}: {e}")
                return jsonify({
                    "id": order_id,
                    "success": False,
                    "message": f"Error processing item {item}: {str(e)}"
                })
            
            if not quantity or quantity <= 0:
                return jsonify({
                    "id": order_id,
                    "success": False,
                    "message": f"Item {item} is out of stock"
                })
        
            # If we get here, the item is available
            return make_response(jsonify({
                "id": order_id,
                "success": True,
                "message": "Item reserved successfully"
            }))
    
    except grpc.RpcError as err:
        logger.error(f'Error reserving inventory: {err.details()}')
        return make_response(
            jsonify({"error": "Internal Server Error", "message": "Failed to reserve inventory"}),
            500
        )
    except Exception as e:
        logger.error(f'Unexpected error reserving inventory: {str(e)}')
        return make_response(
            jsonify({"error": "Internal Server Error", "message": f"Unexpected error: {str(e)}"}),
            500
        )


@app.route('/health', methods=['GET'])
@app.route('/healthz', methods=['GET'])
def health_check():
    return jsonify({
        "service": "inventory-service",
        "status": "healthy",
        "version": "1.0.0"
    })


def main():
    # Start the Flask app server
    app.run(host='0.0.0.0', port=APP_PORT, debug=False)


if __name__ == "__main__":
    logging.basicConfig(
        format='%(asctime)s.%(msecs)03d %(levelname)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
        level=logging.INFO)
    
    # Warn if running in debug mode in production
    if APP_PORT != 3002:  # Assuming non-default port means production
        logging.warning("Running with debug=False in production environment")
    
    main()