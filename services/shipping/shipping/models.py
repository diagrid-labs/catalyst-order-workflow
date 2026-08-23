"""Data types the delivery workflow works with.

A delivery is not an order: it carries only what is needed to get goods to a
customer's door. The order's money — its total — stays with the order-processor.
"""

from dataclasses import dataclass


@dataclass
class Delivery:
    order_id: str
    customer: str
    item: str
    qty: int = 1


@dataclass
class DeliveryResult:
    order_id: str
    dispatched: bool
    message: str


def to_delivery(value) -> Delivery:
    """Rebuild a Delivery from whatever the workflow SDK handed us.

    Inputs cross the wire as JSON and come back as a mapping or a namespace
    depending on where they are received, so the `delivery: Delivery`
    annotations are hints. Rebuilding here makes them honest and lets activities
    use asdict().
    """
    if isinstance(value, Delivery):
        return value
    fields = value if isinstance(value, dict) else vars(value)
    return Delivery(**fields)
