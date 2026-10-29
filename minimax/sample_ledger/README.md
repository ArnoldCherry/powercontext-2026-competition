# sample_ledger

A small, runnable personal-ledger project used by the SDD context demo.

- `ledger/` contains the application code.
- `tests/` contains functional and regression tests.
- `specs/` contains the reporting specification and traceability matrix.
- `tasks/TASK-001.md` is the demo development task.

Run the tests from this directory:

```bash
python -m pytest -q
```

Generate a report:

```bash
python -m ledger report --csv data/sample.csv --month 2026-01 --min-amount 10
```

The baseline state intentionally lacks the refund separation and `min_amount` filtering required by
`REQ-REPORT-004` and `REQ-REPORT-005`; the target tests fail until the MiniMax-assisted change is applied.
