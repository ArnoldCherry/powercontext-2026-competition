from datetime import date
from decimal import Decimal

import pytest

from ledger.models import Transaction


@pytest.fixture
def positive_transactions() -> list[Transaction]:
    return [
        Transaction(date(2026, 1, 3), "food", Decimal("40.00"), "lunch"),
        Transaction(date(2026, 1, 5), "food", Decimal("60.00"), "groceries"),
        Transaction(date(2026, 1, 8), "travel", Decimal("50.00"), "train"),
        Transaction(date(2026, 1, 12), "books", Decimal("5.00"), "notebook"),
        Transaction(date(2026, 2, 1), "food", Decimal("99.00"), "next month"),
    ]


@pytest.fixture
def mixed_transactions() -> list[Transaction]:
    return [
        Transaction(date(2026, 1, 3), "food", Decimal("40.00"), "lunch"),
        Transaction(date(2026, 1, 5), "food", Decimal("60.00"), "groceries"),
        Transaction(date(2026, 1, 8), "travel", Decimal("50.00"), "train"),
        Transaction(date(2026, 1, 10), "food", Decimal("-15.00"), "refund"),
        Transaction(date(2026, 1, 12), "books", Decimal("5.00"), "notebook"),
        Transaction(date(2026, 2, 1), "food", Decimal("99.00"), "next month"),
    ]
