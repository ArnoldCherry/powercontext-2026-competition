from decimal import Decimal

import pytest

from ledger.csv_io import load_transactions
from ledger.models import LedgerError


@pytest.mark.sdd("REQ-REPORT-001", kind="regression")
def test_load_transactions_reads_bom_and_blank_lines(tmp_path):
    csv_path = tmp_path / "ledger.csv"
    csv_path.write_bytes(
        "\ufeffdate,category,amount,note\n"
        "2026-01-03,food,40.00,lunch\n"
        "\n"
        "2026-01-05,food,60.00,groceries\n".encode("utf-8")
    )

    transactions = load_transactions(csv_path)

    assert len(transactions) == 2
    assert transactions[1].category == "food"
    assert transactions[1].amount == Decimal("60.00")


@pytest.mark.sdd("REQ-REPORT-001", kind="regression")
def test_load_transactions_rejects_bad_amount(tmp_path):
    csv_path = tmp_path / "broken.csv"
    csv_path.write_text("date,category,amount,note\n2026-01-03,food,abc,lunch\n", encoding="utf-8")

    with pytest.raises(LedgerError, match="line 2"):
        load_transactions(csv_path)


@pytest.mark.sdd("REQ-REPORT-001", kind="regression")
def test_load_transactions_rejects_wrong_header(tmp_path):
    csv_path = tmp_path / "wrong.csv"
    csv_path.write_text("date,kind,amount,note\n2026-01-03,food,1.00,x\n", encoding="utf-8")

    with pytest.raises(LedgerError, match="expected columns"):
        load_transactions(csv_path)
