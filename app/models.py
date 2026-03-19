from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class Customer:
    id: int
    external_ref: str
    name: str
    created_at: str


@dataclass(slots=True)
class Account:
    id: int
    customer_id: int | None
    account_number: str
    name: str
    account_type: str
    currency: str
    overdraft_limit: Decimal
    is_internal: bool
    created_at: str


@dataclass(slots=True)
class LedgerEntry:
    id: int
    transaction_id: int
    account_id: int
    direction: str
    amount: Decimal
    posted_at: str
    created_at: str


@dataclass(slots=True)
class LedgerTransaction:
    id: int
    external_ref: str
    transaction_type: str
    description: str
    currency: str
    posted_at: str
    reversal_of_transaction_id: int | None
    entries: list[LedgerEntry]


def decimalize(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))
