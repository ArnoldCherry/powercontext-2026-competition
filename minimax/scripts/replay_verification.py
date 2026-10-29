#!/usr/bin/env python3
"""Replay the pre-change failure and post-change success without DB or model access."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sdd_context.runner import (  # noqa: E402
    error_count,
    failed_count,
    failed_node_ids,
    passed_count,
    run_pytest,
    test_file_hashes,
)


def main() -> int:
    baseline_path = ROOT / "evidence" / "baseline" / "baseline.json"
    if not baseline_path.is_file():
        print(f"ERROR: missing {baseline_path}", file=sys.stderr)
        return 2
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    target_ids = baseline["target_tests"]
    regression_ids = baseline["regression_tests"]
    snapshot_path = ROOT / baseline["code_snapshots"]["ledger/report.py"]["snapshot"]
    sample_root = ROOT / "sample_ledger"
    final_path = sample_root / "ledger" / "report.py"
    final_content = final_path.read_text(encoding="utf-8")
    snapshot_content = snapshot_path.read_text(encoding="utf-8")
    output_dir = ROOT / "evidence" / "replay"
    output_dir.mkdir(parents=True, exist_ok=True)
    before_hashes = test_file_hashes(sample_root)

    try:
        final_path.write_text(snapshot_content, encoding="utf-8", newline="\n")
        before_target = run_pytest(sample_root, target_ids, output_dir / "before-target.txt")
        before_regression = run_pytest(
            sample_root, regression_ids, output_dir / "before-regression.txt"
        )
        before_target_failed = failed_node_ids(before_target["output"])
        before_ok = (
            before_target["exit_code"] != 0
            and set(target_ids).issubset(before_target_failed)
            and error_count(before_target["output"]) == 0
            and before_regression["exit_code"] == 0
        )

        final_path.write_text(final_content, encoding="utf-8", newline="\n")
        after_target = run_pytest(sample_root, target_ids, output_dir / "after-target.txt")
        after_regression = run_pytest(
            sample_root, regression_ids, output_dir / "after-regression.txt"
        )
        after_full = run_pytest(sample_root, [], output_dir / "after-full.txt")
        after_hashes = test_file_hashes(sample_root)
        after_ok = (
            after_target["exit_code"] == 0
            and passed_count(after_target["output"]) == len(target_ids)
            and after_regression["exit_code"] == 0
            and passed_count(after_regression["output"]) == len(regression_ids)
            and after_full["exit_code"] == 0
        )
        hashes_unchanged = before_hashes == after_hashes
        baseline_hashes_match = before_hashes == baseline.get("test_file_hashes", {})
        ok = before_ok and after_ok and hashes_unchanged and baseline_hashes_match
        summary = {
            "schema": "sdd.replay.v1",
            "ok": ok,
            "baseline_commit": baseline.get("git", {}).get("head"),
            "source_snapshot": str(snapshot_path.relative_to(ROOT)),
            "target_tests": target_ids,
            "regression_tests": regression_ids,
            "before": {
                "target_exit_code": before_target["exit_code"],
                "target_failed": failed_count(before_target["output"]),
                "target_failed_node_ids": sorted(before_target_failed),
                "regression_exit_code": before_regression["exit_code"],
                "regression_passed": passed_count(before_regression["output"]),
                "target_log": str((output_dir / "before-target.txt").relative_to(ROOT)),
                "regression_log": str((output_dir / "before-regression.txt").relative_to(ROOT)),
            },
            "after": {
                "target_exit_code": after_target["exit_code"],
                "target_passed": passed_count(after_target["output"]),
                "regression_exit_code": after_regression["exit_code"],
                "regression_passed": passed_count(after_regression["output"]),
                "full_exit_code": after_full["exit_code"],
                "full_passed": passed_count(after_full["output"]),
                "target_log": str((output_dir / "after-target.txt").relative_to(ROOT)),
                "regression_log": str((output_dir / "after-regression.txt").relative_to(ROOT)),
                "full_log": str((output_dir / "after-full.txt").relative_to(ROOT)),
            },
            "checks": {
                "before_failed_as_expected": before_ok,
                "after_passed": after_ok,
                "test_files_unchanged": hashes_unchanged,
                "baseline_test_hashes_match": baseline_hashes_match,
            },
            "test_file_hashes": after_hashes,
        }
    finally:
        final_path.write_text(final_content, encoding="utf-8", newline="\n")

    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
