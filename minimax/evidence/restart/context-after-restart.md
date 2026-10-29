# SDD context: TASK-001 月报分离退款并支持最低金额过滤

- Context SHA-256: `1d93b61f0ea0ba974e82fcecdabf9451db858adadbdaa7be376764bd9698f0bb`
- Database: 5.7.25-OceanBase seekdb-v1.4.0.0
- Sources verified: True

## Specifications
- REQ-REPORT-004 退款与支出分离 - `specs/reporting.md:17-19` (score 41, keywords: net_total, refund, refund_total, 负数, 退款)
- REQ-REPORT-005 按最低金额过滤支出 - `specs/reporting.md:21-27` (score 26, keywords: min_amount, 最低金额, 过滤, 阈值)
- REQ-REPORT-001 按分类汇总正数支出 - `specs/reporting.md:5-7` (score 23, keywords: total, 分类, 支出, 月报, 汇总)
- REQ-REPORT-002 只统计指定月份 - `specs/reporting.md:9-11` (score 8, keywords: 月份, 过滤)
- REQ-REPORT-003 分类合计稳定排序 - `specs/reporting.md:13-15` (score 4, keywords: 排序)

## Code
- `ledger/report.py::monthly_report` - def monthly_report(transactions: list[Transaction], month: str, min_amount: Decimal | None=None) -> dict[str, object]

## Tests
- [target] `tests/test_cli.py::test_cli_report_includes_refund_fields` - reqs: REQ-REPORT-004
- [target] `tests/test_report.py::test_monthly_report_applies_min_amount` - reqs: REQ-REPORT-005
- [target] `tests/test_report.py::test_monthly_report_separates_refunds` - reqs: REQ-REPORT-004
- [regression] `tests/test_cli.py::test_cli_report_prints_positive_month` - reqs: REQ-REPORT-001
- [regression] `tests/test_csv_io.py::test_load_transactions_reads_bom_and_blank_lines` - reqs: REQ-REPORT-001
- [regression] `tests/test_csv_io.py::test_load_transactions_rejects_bad_amount` - reqs: REQ-REPORT-001
- [regression] `tests/test_csv_io.py::test_load_transactions_rejects_wrong_header` - reqs: REQ-REPORT-001
- [regression] `tests/test_report.py::test_monthly_report_filters_month` - reqs: REQ-REPORT-002
- [regression] `tests/test_report.py::test_monthly_report_groups_positive_expenses` - reqs: REQ-REPORT-001
- [regression] `tests/test_report.py::test_monthly_report_handles_empty_month` - reqs: REQ-REPORT-002
- [regression] `tests/test_report.py::test_monthly_report_sorts_categories` - reqs: REQ-REPORT-003

## PowerContext state
- Scope: `scp_1qe98dqbhewyfxj5jjktxzcccr`
- Completed: context, baseline, patch, validation
- Pending: (none)
- validation: [TASK-001] phase=validation recorded_at=2026-10-29T14:43:24+00:00
- baseline: [TASK-001] phase=baseline recorded_at=2026-10-29T12:42:20+00:00
