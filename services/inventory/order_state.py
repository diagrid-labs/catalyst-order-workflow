"""Shared helpers for reposition order state in the Dapr state store.

Two kinds of keys are written:
  - reposition:<order_id> = full per-order record (see fields below)
  - repositions:active    = list of order ids currently in-flight

Used by both the workflow activities and the HTTP read endpoints.
"""

import json
import os
from datetime import datetime, timezone

from dapr.clients import DaprClient

STATESTORE_NAME = os.getenv("STATESTORE_NAME", "statestore")
LEAD_TIME_MIN = int(os.getenv("LEAD_TIME_MIN", 5))

ACTIVE_ORDERS_KEY = "repositions:active"


def order_key(order_id: str) -> str:
    return f"reposition:{order_id}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_active_orders(d: DaprClient) -> list:
    resp = d.get_state(STATESTORE_NAME, ACTIVE_ORDERS_KEY)
    if not resp.data:
        return []
    try:
        return json.loads(resp.data.decode("utf-8"))
    except (ValueError, AttributeError):
        return []


def write_active_orders(d: DaprClient, active: list) -> None:
    d.save_state(STATESTORE_NAME, ACTIVE_ORDERS_KEY, json.dumps(active))


def read_order(d: DaprClient, order_id: str) -> dict | None:
    resp = d.get_state(STATESTORE_NAME, order_key(order_id))
    if not resp.data:
        return None
    return json.loads(resp.data.decode("utf-8"))


def write_order(d: DaprClient, order: dict) -> None:
    d.save_state(STATESTORE_NAME, order_key(order["id"]), json.dumps(order))
