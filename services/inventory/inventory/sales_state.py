"""Sales-history aggregation.

The inventory service is the source of truth for sales: `/reserve` records each
unit sold here (atomically with the stock decrement). The history is exposed
via `GET /api/v1/inventory/sales/history` and rolled up into a forecast via
`GET /api/v1/inventory/forecast`.

Keys: `sales:<sku>` — capped list of `{qty, ts}` entries.
"""

import json
import math
import os
from datetime import datetime, timezone

from dapr.clients import DaprClient
from dapr.clients.grpc._response import StateResponse

from inventory.state import STATESTORE_NAME

SMA_WINDOW_MIN = int(os.getenv("SMA_WINDOW_MIN", 5))
HORIZON_MIN = int(os.getenv("HORIZON_MIN", 6))
HISTORY_CAP = 60  # ~last hour at 1 sale/minute


def sales_key(sku: str) -> str:
    return f"sales:{sku.lower()}"


def _decode_history(resp: StateResponse) -> list[dict]:
    if not resp.data:
        return []
    try:
        return json.loads(resp.data.decode("utf-8"))
    except (ValueError, AttributeError):
        return []


def read_sales(d: DaprClient, sku: str) -> list[dict]:
    return _decode_history(d.get_state(STATESTORE_NAME, sales_key(sku)))


def encode_appended(history: list[dict], qty: int, ts: str) -> str:
    """Return the JSON-encoded history with `{qty, ts}` appended and capped
    at HISTORY_CAP entries (oldest dropped first).
    """
    history.append({"qty": qty, "ts": ts})
    return json.dumps(history[-HISTORY_CAP:])


def summarize(d: DaprClient, sku: str, window_min: int) -> dict:
    history = read_sales(d, sku)
    cutoff = datetime.now(timezone.utc).timestamp() - window_min * 60
    recent = [
        s for s in history
        if datetime.fromisoformat(s["ts"]).timestamp() >= cutoff
    ]
    total = sum(int(s["qty"]) for s in recent)
    return {
        "sku": sku,
        "window_min": window_min,
        "total_units": total,
        "rate_per_min": total / window_min if window_min else 0.0,
        "samples": recent,
    }


def forecast(
    d: DaprClient,
    sku: str,
    horizon_min: int,
    window_min: int = SMA_WINDOW_MIN,
) -> dict:
    summary = summarize(d, sku, window_min)
    rate = summary["rate_per_min"]
    return {
        "sku": sku,
        "horizon_min": horizon_min,
        "window_min": window_min,
        "rate_per_min": rate,
        "forecast_units": math.ceil(rate * horizon_min),
    }
