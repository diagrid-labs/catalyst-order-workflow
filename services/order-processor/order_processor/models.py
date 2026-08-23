"""Data types the order workflow works with.

The dataclasses cross activity and workflow boundaries as JSON, so `to_order`
exists to rebuild an Order on the far side of one.
"""

import random
import string
from dataclasses import dataclass


@dataclass
class Order:
    id: str
    customer: str
    item: str
    total: float = 0.0
    qty: int = 1

    def __post_init__(self):
        if not self.id:
            raise ValueError("id required")
        if not self.customer:
            raise ValueError("customer required")
        if not self.item:
            raise ValueError("item required")
        if self.qty < 1:
            raise ValueError("qty must be >= 1")
        if self.total < 0:
            raise ValueError("total must be >= 0")

    @classmethod
    def new(cls, *, customer: str, item: str, total: float = 0.0, qty: int = 1) -> "Order":
        """Build a new Order with a freshly generated id.

        Reconstructing an existing order (e.g. from workflow state) should use
        the regular constructor with `id` supplied.
        """
        if not customer:
            raise ValueError("customer required")
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=5))
        return cls(
            id=f"order_{customer.lower()}_{suffix}",
            customer=customer,
            item=item,
            total=total,
            qty=qty,
        )


@dataclass
class Approval:
    approver: str
    approved: bool

    def __post_init__(self):
        if not self.approver:
            raise ValueError("approver required")
        if not isinstance(self.approved, bool):
            raise ValueError("approved must be a boolean")


@dataclass
class OrderResult:
    id: str
    success: bool
    message: str


@dataclass
class InventoryResult:
    id: str
    success: bool
    message: str
    remaining: int | None = None


def to_order(value) -> Order:
    """Rebuild an Order at an activity boundary.

    The Dapr workflow SDK serializes dataclass inputs to JSON and deserializes
    them back as a namespace or a mapping, so the `order: Order` annotation on
    activity functions is a hint, not a runtime guarantee. Rebuilding the
    dataclass here makes the hint honest and lets us use asdict() consistently
    for re-serialization.
    """
    if isinstance(value, Order):
        return value
    fields = value if isinstance(value, dict) else vars(value)
    return Order(**fields)
