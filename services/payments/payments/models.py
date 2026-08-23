"""Data types the payment workflow works with.

A payment is not an order: it carries who is paying and how much. What they
bought stays with the order-processor.
"""

from dataclasses import dataclass


@dataclass
class Payment:
    order_id: str
    customer: str
    total: float


@dataclass
class PaymentResult:
    order_id: str
    success: bool
    message: str


def to_payment(value) -> Payment:
    """Rebuild a Payment from whatever the workflow SDK handed us.

    Inputs cross the wire as JSON and come back as a mapping or a namespace
    depending on where they are received, so the `payment: Payment` annotations
    are hints. Rebuilding here makes them honest and lets activities use
    asdict().
    """
    if isinstance(value, Payment):
        return value
    fields = value if isinstance(value, dict) else vars(value)
    return Payment(**fields)
