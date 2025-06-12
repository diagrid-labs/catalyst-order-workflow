import logging
import os
from flask import Flask, request, jsonify, make_response

APP_PORT = int(os.getenv("APP_PORT", 3003))

app = Flask(__name__)

@app.post('/api/v1/payments')
def create_charge():
    if not request.is_json:
        return make_response(
            jsonify({"error": "Bad Request", "message": "Request must be JSON"}),
            400
        )
    
    order_data = request.json

    if not order_data or not isinstance(order_data, dict):
        return make_response(
            jsonify({"error": "Bad Request", "message": "Invalid order format"}),
            400
        )
    
    logging.info(f"Processing payment charge for order: {order_data}")
    
    return make_response(
        jsonify({"id": order_data['id'], "success": True, "message": "Payment processed successfully"}),
        201
    )


@app.route('/api/v1/payments/<id>/refunds', methods=['POST'])
def create_refund(id):
    if not request.is_json:
        return make_response(
            jsonify({"error": "Bad Request", "message": "Request must be JSON"}),
            400
        )
    
    refund_data = request.json

    if not refund_data or not isinstance(refund_data, dict):
        return make_response(
            jsonify({"error": "Bad Request", "message": "Invalid refund format"}),
            400
        )
    
    logging.info(f"Processing refund for payment: {id}")
    
    return make_response(
        jsonify({"status": "success", "message": "Refund processed successfully"}),
        200
    )

# Health check endpoint
@app.get('/health')
@app.get('/healthz')
def health_check():
    return jsonify({
        "service": "payment-service",
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
    if APP_PORT != 3003:  # Assuming non-default port means production
        logging.warning("Running with debug=False in production environment")
    
    main()
