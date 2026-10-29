from __future__ import annotations

from decimal import Decimal

from .models import Transaction


def monthly_report(
    transactions: list[Transaction],
    month: str,
    min_amount: Decimal | None = None,
) -> dict[str, object]:
    """Summarize one YYYY-MM month into category totals."""
    selected = [
        transaction
        for transaction in transactions
        if transaction.occurred_on.strftime("%Y-%m") == month
    ]
    totals: dict[str, Decimal] = {}
    for transaction in selected:
        totals[transaction.category] = (
            totals.get(transaction.category, Decimal("0")) + transaction.amount
        )
    total = sum(totals.values(), Decimal("0"))
    return {
        "month": month,
        "categories": dict(sorted(totals.items())),
        "total": total,
    }
