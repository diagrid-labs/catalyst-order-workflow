from datetime import datetime
from enum import Enum
from typing import Optional, List
from dataclasses import dataclass
from pydantic import BaseModel

@dataclass
class Order:
    id: str
    customer: str
    item: str
    total: float
