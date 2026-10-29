from __future__ import annotations

import argparse
import json
from decimal import Decimal

from .csv_io import load_transactions
from .report import monthly_report


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger", description="Personal ledger reports")
    commands = parser.add_subparsers(dest="command", required=True)
    report = commands.add_parser("report", help="summarize one month")
    report.add_argument("--csv", required=True, help="transaction CSV file")
    report.add_argument("--month", required=True, help="month in YYYY-MM form")
    report.add_argument("--min-amount", type=Decimal, default=None, help="minimum expense amount")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "report":
        transactions = load_transactions(args.csv)
        result = monthly_report(transactions, args.month, min_amount=args.min_amount)
        print(json.dumps(_jsonable(result), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return 2
