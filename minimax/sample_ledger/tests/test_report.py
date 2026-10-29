from decimal import Decimal

import pytest

from ledger.report import monthly_report


@pytest.mark.sdd("REQ-REPORT-001", kind="regression")
def test_monthly_report_groups_positive_expenses(positive_transactions):
    report = monthly_report(positive_transactions, "2026-01")

    assert report["categories"] == {
        "books": Decimal("5.00"),
        "food": Decimal("100.00"),
        "travel": Decimal("50.00"),
    }
    assert report["total"] == Decimal("155.00")


@pytest.mark.sdd("REQ-REPORT-002", kind="regression")
def test_monthly_report_filters_month(positive_transactions):
    january = monthly_report(positive_transactions, "2026-01")
    february = monthly_report(positive_transactions, "2026-02")

    assert january["total"] == Decimal("155.00")
    assert february["categories"] == {"food": Decimal("99.00")}
    assert february["total"] == Decimal("99.00")


@pytest.mark.sdd("REQ-REPORT-003", kind="regression")
def test_monthly_report_sorts_categories(positive_transactions):
    report = monthly_report(positive_transactions, "2026-01")

    assert list(report["categories"]) == ["books", "food", "travel"]


@pytest.mark.sdd("REQ-REPORT-002", kind="regression")
def test_monthly_report_handles_empty_month(positive_transactions):
    report = monthly_report(positive_transactions, "2025-12")

    assert report["categories"] == {}
    assert report["total"] == Decimal("0")


@pytest.mark.sdd("REQ-REPORT-004", kind="target")
def test_monthly_report_separates_refunds(mixed_transactions):
    report = monthly_report(mixed_transactions, "2026-01")

    assert report["categories"] == {
        "food": Decimal("100.00"),
        "travel": Decimal("50.00"),
        "books": Decimal("5.00"),
    }
    assert report["total"] == Decimal("155.00")
    assert report.get("refund_total") == Decimal("15.00")
    assert report.get("net_total") == Decimal("140.00")


@pytest.mark.sdd("REQ-REPORT-005", kind="target")
def test_monthly_report_applies_min_amount(mixed_transactions):
    report = monthly_report(mixed_transactions, "2026-01", min_amount=Decimal("10"))

    assert report["categories"] == {
        "food": Decimal("100.00"),
        "travel": Decimal("50.00"),
    }
    assert report["total"] == Decimal("150.00")
    assert report.get("refund_total") == Decimal("15.00")
    assert report.get("net_total") == Decimal("135.00")
