import json

import pytest

from ledger.cli import main

HEADER = "date,category,amount,note\n"


def write_csv(path, rows: str) -> str:
    path.write_text(HEADER + rows, encoding="utf-8")
    return str(path)


@pytest.mark.sdd("REQ-REPORT-001", kind="regression")
def test_cli_report_prints_positive_month(tmp_path, capsys):
    csv_path = write_csv(
        tmp_path / "positive.csv",
        "2026-01-03,food,40.00,lunch\n2026-01-05,food,60.00,groceries\n",
    )

    exit_code = main(["report", "--csv", csv_path, "--month", "2026-01"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["month"] == "2026-01"
    assert payload["categories"] == {"food": "100.00"}
    assert payload["total"] == "100.00"


@pytest.mark.sdd("REQ-REPORT-004", kind="target")
def test_cli_report_includes_refund_fields(tmp_path, capsys):
    csv_path = write_csv(
        tmp_path / "mixed.csv",
        "2026-01-03,food,40.00,lunch\n"
        "2026-01-05,food,60.00,groceries\n"
        "2026-01-10,food,-15.00,refund\n",
    )

    exit_code = main(
        ["report", "--csv", csv_path, "--month", "2026-01", "--min-amount", "10"]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["categories"] == {"food": "100.00"}
    assert payload["total"] == "100.00"
    assert payload["refund_total"] == "15.00"
    assert payload["net_total"] == "85.00"
