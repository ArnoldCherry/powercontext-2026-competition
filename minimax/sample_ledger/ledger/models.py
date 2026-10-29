from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class LedgerError(ValueError):
    """Raised when ledger input cannot be interpreted."""


@dataclass(frozen=True, slots=True)
class Transaction:
    occurred_on: date
    category: str
    amount: Decimal
    note: str = ""
