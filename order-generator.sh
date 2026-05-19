#!/bin/bash

# Advanced script to generate order API calls with configurable parameters

# Default values
HOST="localhost"
PORT="3006"
ENDPOINT="/orders"
INVENTORY_PORT="3002"
RATE=2  # requests per second
DURATION=0  # run forever by default
VERBOSE=true

# Parse command line arguments
while getopts "h:p:e:r:d:I:q" opt; do
  case $opt in
    h) HOST="$OPTARG" ;;
    p) PORT="$OPTARG" ;;
    e) ENDPOINT="$OPTARG" ;;
    r) RATE="$OPTARG" ;;
    d) DURATION="$OPTARG" ;;
    I) INVENTORY_PORT="$OPTARG" ;;
    q) VERBOSE=false ;;
    \?) echo "Invalid option -$OPTARG" >&2; exit 1 ;;
  esac
done

# Arrays of sample data
CUSTOMERS=("alice" "bob" "charlie" "david" "emma" "frank" "grace" "hannah" "ian" "julia"
           "kevin" "lucy" "michael" "nina" "oliver" "penny" "quinn" "robert" "sarah" "thomas")

# Fetch the current inventory from the inventory service so the generator
# only ever orders SKUs that actually exist in the catalog.
inventory_url="${HOST}:${INVENTORY_PORT}/api/v1/inventory"
inventory_json=$(curl -sf "$inventory_url") || {
  echo "Failed to fetch inventory from $inventory_url" >&2
  exit 1
}
mapfile -t ITEMS < <(echo "$inventory_json" | python3 -c "import sys, json
data = json.load(sys.stdin)
if isinstance(data, list):
    for entry in data:
        name = entry.get('name')
        if name:
            print(name)")
if [ ${#ITEMS[@]} -eq 0 ]; then
  echo "Inventory is empty — restock first via POST $inventory_url/restock" >&2
  exit 1
fi

# Calculate sleep time between requests
SLEEP_TIME=$(awk -v rate="$RATE" 'BEGIN{print 1/rate}')

# Print configuration
if $VERBOSE; then
  echo "=== Order Generator Configuration ==="
  echo "Target: $HOST:$PORT$ENDPOINT"
  echo "Service Host: $SERVICE_HOST"
  echo "Inventory: $inventory_url"
  echo "Items: ${ITEMS[*]}"
  echo "Rate: $RATE requests per second"
  echo "Qty distribution: 80% in 1..10, 20% in 10..50"
  if [ "$DURATION" -gt 0 ]; then
    echo "Duration: $DURATION seconds"
  else
    echo "Duration: Running until stopped (Ctrl+C)"
  fi
  echo "======================================"
fi

# Initialize counters
count=0
start_time=$(date +%s)

# Trap Ctrl+C to print summary before exiting
trap 'echo -e "\n=== Summary ===\nTotal orders sent: $count\nRunning time: $(($(date +%s) - start_time)) seconds\n==============="; exit' INT

# Main loop
while true; do
  # Check if we've reached the duration limit
  current_time=$(date +%s)
  elapsed=$((current_time - start_time))
  if [ "$DURATION" -gt 0 ] && [ "$elapsed" -ge "$DURATION" ]; then
    if $VERBOSE; then
      echo -e "\n=== Duration limit reached ==="
      echo "Total orders sent: $count"
      echo "Running time: $elapsed seconds"
      echo "============================"
    fi
    break
  fi
  
  # Select random customer, item, and quantity. Quantities follow a realistic
  # distribution — most orders are small (1..10), occasionally a bulk order
  # (10..50).
  customer=${CUSTOMERS[$RANDOM % ${#CUSTOMERS[@]}]}
  item=${ITEMS[$RANDOM % ${#ITEMS[@]}]}
  if [ $((RANDOM % 100)) -lt 80 ]; then
    qty=$(( (RANDOM % 10) + 1 ))
  else
    qty=$(( (RANDOM % 41) + 10 ))
  fi

  # Construct and execute the API call. The order-processor prices the order
  # from the inventory catalog (unit price * qty) when no total is provided.
  response=$(curl -s -w "\n%{http_code}" -X POST $HOST:$PORT$ENDPOINT \
    -H "Host: $SERVICE_HOST" \
    -H "Content-Type: application/json" \
    -d "{\"customer\": \"$customer\", \"item\": \"$item\", \"qty\": $qty}")

  # Extract status code and response body
  status_code=$(echo "$response" | tail -n1)
  response_body=$(echo "$response" | sed '$d')

  count=$((count + 1))

  # Print output if verbose mode is enabled
  if $VERBOSE; then
    timestamp=$(date "+%Y-%m-%d %H:%M:%S")
    if [ "$status_code" -ge 200 ] && [ "$status_code" -lt 300 ]; then
      echo "[$timestamp] #$count - $customer ordered $item x$qty - Success ($status_code)"
    else
      echo "[$timestamp] #$count - $customer ordered $item x$qty - Failed ($status_code)"
      echo "Response: $response_body"
    fi
  fi

  # Sleep until next request
  sleep $SLEEP_TIME
done