"""State-store wiring shared by the inventory service's modules."""

import os
from datetime import datetime, timezone

STATESTORE_NAME = os.getenv("STATESTORE_NAME", "statestore")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
