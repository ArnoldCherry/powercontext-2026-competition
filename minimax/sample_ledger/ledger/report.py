from __future__ import annotations

from decimal import Decimal

from .models import Transaction


def monthly_report(
    transactions: list[Transaction],
    month: str,
    min_amount: Decimal | None = None,
) -> dict[str, object]:
    """Summarize one YYYY-MM month into expense and refund totals."""
    selected = [
        transaction
        for transaction in transactions
        if transaction.occurred_on.strftime("%Y-%m") == month
    ]

    totals: dict[str, Decimal] = {}
    refund_total = Decimal("0")

    for transaction in selected:
        amount = transaction.amount
        if amount < Decimal("0"):
            refund_total += -amount
        elif amount > Decimal("0") and (
            min_amount is None or amount >= min_amount
        ):
            totals[transaction.category] = (
                totals.get(transaction.category, Decimal("0")) + amount
            )

    total = sum(totals.values(), Decimal("0"))
    return {
        "month": month,
        "categories": dict(sorted(totals.items())),
        "total": total,
        "refund_total": refund_total,
        "net_total": total - refund_total,
    }
