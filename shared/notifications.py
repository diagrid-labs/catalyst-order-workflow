"""Publishing order progress for the notifications UI.

The order workflow and the delivery workflow both report progress against the
order they are working on, and the notifications service subscribes to the
topic they publish to (see `resources/subscription.yaml`). The component and
topic names live here so the two publishers cannot drift apart.

Each service keeps its own `notify` activity: what is shared is the publish
call, not the workflow step. An activity decides what to say and which order to
say it about; this decides where it goes.
"""

import json
import os

from dapr.clients import DaprClient

PUBSUB_NAME = os.getenv("PUBSUB_NAME", "pubsub")
TOPIC_NAME = os.getenv("TOPIC_NAME", "notifications")


def publish(order_id: str, message: str) -> None:
    """Publish one progress message against an order."""
    with DaprClient() as d:
        d.publish_event(PUBSUB_NAME, TOPIC_NAME, json.dumps({
            "order_id": order_id,
            "message": message,
            "data-content-type": "application/json",
        }))
