from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import LedgerError, Transaction

COLUMNS = ("date", "category", "amount", "note")


def load_transactions(path: str | Path) -> list[Transaction]:
    source = Path(path)
    try:
        handle = source.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        raise LedgerError(f"cannot open {source}: {exc}") from exc

    with handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(COLUMNS):
            raise LedgerError(
                f"{source}: expected columns {', '.join(COLUMNS)}; got {reader.fieldnames}"
            )

        transactions: list[Transaction] = []
        for line_number, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            try:
                occurred_on = date.fromisoformat((row["date"] or "").strip())
                category = (row["category"] or "").strip()
                if not category:
                    raise ValueError("category is empty")
                amount = Decimal((row["amount"] or "").strip())
            except (ValueError, InvalidOperation) as exc:
                raise LedgerError(f"{source}: line {line_number}: {exc}") from exc
            if not amount.is_finite():
                raise LedgerError(f"{source}: line {line_number}: amount must be finite")
            transactions.append(
                Transaction(
                    occurred_on=occurred_on,
                    category=category,
                    amount=amount,
                    note=(row["note"] or "").strip(),
                )
            )
        return transactions
