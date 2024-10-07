from datetime import datetime
from enum import Enum
from typing import Optional, List
from dataclasses import dataclass
from pydantic import BaseModel


class AmountMoney(BaseModel):
    amount: int
    currency: str

# Define AppFeeMoney class (optional fee)


class AppFeeMoney(BaseModel):
    amount: int
    currency: str


class CreatePayment(BaseModel):
    amount_money: AmountMoney  # Reference to AmountMoney class
    idempotency_key: str
    source_id: str
    autocomplete: bool
    customer_id: str


@dataclass
class Order:
    id: str
    customer: str
    items: list[str]
    total: float


@dataclass
class PaymentResult:
    success: bool
    message: str
