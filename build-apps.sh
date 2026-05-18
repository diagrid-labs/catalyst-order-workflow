#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Python services
for svc in inventory order-processor payments shipping inventory-agent; do
    (cd "services/$svc" && pip3 install -r requirements.txt)
done

# Node service
(cd services/notifications && npm install)
