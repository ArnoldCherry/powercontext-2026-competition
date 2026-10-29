from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pymysql

from .config import PACKAGE_ROOT, Settings, load_settings
from .context import (
    build_context,
    load_context,
    render_markdown,
    test_node_ids,
)
from .db import connect, ensure_database, ensure_schema, list_tables, reset_schema, server_version
from .ingest import import_project
from .minimax_client import MiniMaxClient, MiniMaxError, assist
from .parsing import parse_task, sha256_text
from .powercontext import (
    PowerContextClient,
    PowerContextError,
    record_phase,
    state_from_entries,
)
from .runner import (
    error_count,
    failed_count,
    failed_node_ids,
    git_diff,
    git_info,
    passed_count,
    run_pytest,
    sha256_file,
    test_file_hashes,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _settings(args: argparse.Namespace) -> Settings:
    return load_settings(
        project_root=Path(args.project) if args.project else None,
        env_file=Path(args.env_file) if args.env_file else None,
        runtime_dir=Path(args.runtime) if args.runtime else None,
        evidence_dir=Path(args.evidence) if args.evidence else None,
    )


def _connection(settings: Settings):
    ensure_database(settings)
    connection = connect(settings)
    ensure_schema(connection)
    return connection


def _client(settings: Settings) -> PowerContextClient:
    return PowerContextClient(settings.powercontext_url, settings.powercontext_token)


def _default_task(settings: Settings) -> Path:
    return settings.project_root / "tasks" / "TASK-001.md"


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(PACKAGE_ROOT).as_posix()
    except ValueError:
        return str(path)


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
        newline="\n",
    )


def _load_or_build_context(settings: Settings, args: argparse.Namespace, connection, client):
    context_path = Path(args.context) if args.context else settings.runtime_dir / "context.json"
    if context_path.is_file():
        return load_context(context_path), context_path
    context = build_context(settings, args.task or _default_task(settings), connection, client)
    _write_json(context_path, context)
    return context, context_path


def _baseline_log(settings: Settings) -> str:
    parts = []
    for name in ("target-tests.txt", "regression-tests.txt", "full-suite.txt"):
        path = settings.evidence_dir / "baseline" / name
        if path.is_file():
            parts.append(f"## {name}\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def cmd_init(args: argparse.Namespace) -> int:
    settings = _settings(args)
    connection = _connection(settings)
    try:
        task = parse_task(args.task or _default_task(settings))
        client = _client(settings)
        scope_id = client.ensure_scope(settings.runtime_dir, task.task_id)
        print(
            json.dumps(
                {
                    "ok": True,
                    "seekdb": {
                        "host": settings.seekdb_host,
                        "port": settings.seekdb_port,
                        "database": settings.seekdb_database,
                        "version": server_version(connection),
                        "tables": list_tables(connection),
                    },
                    "powercontext": {"url": settings.powercontext_url, "scope_id": scope_id},
                    "project": str(settings.project_root),
                    "task": task.task_id,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        connection.close()


def cmd_import(args: argparse.Namespace) -> int:
    settings = _settings(args)
    connection = _connection(settings)
    try:
        summary = import_project(connection, settings.project_root)
        _write_json(settings.runtime_dir / "import-summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        connection.close()


def cmd_context(args: argparse.Namespace) -> int:
    settings = _settings(args)
    connection = _connection(settings)
    try:
        client = _client(settings)
        context = build_context(
            settings,
            args.task or _default_task(settings),
            connection,
            client,
        )
        output = Path(args.output) if args.output else settings.runtime_dir / "context.json"
        _write_json(output, context)
        markdown = render_markdown(context)
        markdown_path = output.with_suffix(".md")
        markdown_path.write_text(markdown, encoding="utf-8", newline="\n")
        record = None
        if args.record:
            task_id = context["task"]["task_id"]
            state = state_from_entries(
                client.list_entries(context["state"]["scope_id"]), task_id
            )
            record = record_phase(
                client,
                context["state"]["scope_id"],
                task_id,
                "context",
                {
                    "context_sha256": context["query"]["context_sha256"],
                    "matched_req_ids": context["query"]["matched_req_ids"],
                    "specs": [spec["req_id"] for spec in context["sources"]["specs"]],
                    "code": [
                        f"{item['path']}::{item['symbol']}" for item in context["sources"]["code"]
                    ],
                    "target_tests": test_node_ids(context, "target"),
                    "regression_tests": test_node_ids(context, "regression"),
                    "sources_verified": context["validation"]["verified"],
                    "pending_before_record": state["pending"],
                },
                [_rel(output), _rel(markdown_path)],
            )
        summary = {
            "ok": context["validation"]["verified"],
            "output": _rel(output),
            "markdown": _rel(markdown_path),
            "context_sha256": context["query"]["context_sha256"],
            "matched_req_ids": context["query"]["matched_req_ids"],
            "counts": {
                "specs": len(context["sources"]["specs"]),
                "code": len(context["sources"]["code"]),
                "target_tests": len(test_node_ids(context, "target")),
                "regression_tests": len(test_node_ids(context, "regression")),
            },
            "state": {
                "scope_id": context["state"].get("scope_id"),
                "completed": context["state"].get("completed", []),
                "pending": context["state"].get("pending", []),
            },
            "record": record,
        }
        print(markdown)
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 0 if context["validation"]["verified"] else 1
    finally:
        connection.close()


def cmd_baseline(args: argparse.Namespace) -> int:
    settings = _settings(args)
    connection = _connection(settings)
    try:
        client = _client(settings)
        context, context_path = _load_or_build_context(settings, args, connection, client)
        target_ids = test_node_ids(context, "target")
        regression_ids = test_node_ids(context, "regression")
        if not target_ids:
            raise RuntimeError("context contains no target tests")
        baseline_dir = settings.evidence_dir / "baseline"
        baseline_dir.mkdir(parents=True, exist_ok=True)
        target = run_pytest(settings.project_root, target_ids, baseline_dir / "target-tests.txt")
        regression = run_pytest(
            settings.project_root, regression_ids, baseline_dir / "regression-tests.txt"
        )
        full = run_pytest(settings.project_root, [], baseline_dir / "full-suite.txt")

        target_failed = failed_node_ids(target["output"])
        target_ok = (
            target["exit_code"] != 0
            and set(target_ids).issubset(target_failed)
            and error_count(target["output"]) == 0
        )
        regression_ok = regression["exit_code"] == 0 and failed_count(regression["output"]) == 0
        full_ok = full["exit_code"] != 0
        ok = target_ok and regression_ok and full_ok

        code_snapshots = {}
        for item in context["sources"]["code"]:
            source = settings.project_root / item["path"]
            destination = baseline_dir / "code" / item["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            code_snapshots[item["path"]] = {
                "snapshot": _rel(destination),
                "sha256": sha256_file(destination),
            }
        test_hashes = test_file_hashes(settings.project_root)
        info = git_info(settings.project_root)
        summary = {
            "schema": "sdd.baseline.v1",
            "created_at": _timestamp(),
            "ok": ok,
            "context": _rel(context_path),
            "context_sha256": context["query"]["context_sha256"],
            "database_version": context["database"]["version"],
            "git": info,
            "target_tests": target_ids,
            "regression_tests": regression_ids,
            "test_file_hashes": test_hashes,
            "code_snapshots": code_snapshots,
            "results": {
                "target": {
                    "command": target["command"],
                    "exit_code": target["exit_code"],
                    "failed": failed_count(target["output"]),
                    "passed": passed_count(target["output"]),
                    "failed_node_ids": sorted(target_failed),
                    "log": _rel(baseline_dir / "target-tests.txt"),
                },
                "regression": {
                    "command": regression["command"],
                    "exit_code": regression["exit_code"],
                    "failed": failed_count(regression["output"]),
                    "passed": passed_count(regression["output"]),
                    "log": _rel(baseline_dir / "regression-tests.txt"),
                },
                "full": {
                    "command": full["command"],
                    "exit_code": full["exit_code"],
                    "failed": failed_count(full["output"]),
                    "passed": passed_count(full["output"]),
                    "log": _rel(baseline_dir / "full-suite.txt"),
                },
            },
        }
        _write_json(baseline_dir / "baseline.json", summary)
        if args.record and ok:
            task_id = context["task"]["task_id"]
            record_phase(
                client,
                context["state"]["scope_id"],
                task_id,
                "baseline",
                {
                    "target_exit_code": target["exit_code"],
                    "target_failed": len(target_ids),
                    "target_failed_node_ids": sorted(target_failed),
                    "regression_exit_code": regression["exit_code"],
                    "regression_passed": passed_count(regression["output"]),
                    "full_exit_code": full["exit_code"],
                    "git_head": info["head"],
                    "code_sha256": {
                        path: value["sha256"] for path, value in code_snapshots.items()
                    },
                },
                [
                    _rel(baseline_dir / "baseline.json"),
                    _rel(baseline_dir / "target-tests.txt"),
                    _rel(baseline_dir / "regression-tests.txt"),
                    _rel(baseline_dir / "full-suite.txt"),
                ],
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 0 if ok else 1
    finally:
        connection.close()


def cmd_assist(args: argparse.Namespace) -> int:
    settings = _settings(args)
    baseline_path = settings.evidence_dir / "baseline" / "baseline.json"
    if not baseline_path.is_file():
        raise RuntimeError("baseline evidence is missing; run the baseline command first")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not baseline.get("ok"):
        raise RuntimeError("baseline evidence is not valid; refusing to run the assistant")
    connection = _connection(settings)
    try:
        client = _client(settings)
        context, context_path = _load_or_build_context(settings, args, connection, client)
        target_paths = sorted({item["path"] for item in context["sources"]["code"]})
        if len(target_paths) != 1:
            raise RuntimeError(f"expected one target file, got {target_paths}")
        existing_diff = git_diff(settings.project_root, target_paths)
        if existing_diff.strip():
            raise RuntimeError(f"target file already modified; commit or restore first:\n{existing_diff[:2000]}")
        record = assist(
            settings,
            context,
            context["task"]["body"],
            _baseline_log(settings),
            settings.evidence_dir,
            max_attempts=args.attempts,
        )
        record_phase(
            client,
            context["state"]["scope_id"],
            context["task"]["task_id"],
            "patch",
            {
                "model": record["model"],
                "attempts": len(record["attempts"]),
                "target_file": record["target_file"],
                "before_sha256": record["target_file_before_sha256"],
                "after_sha256": record["target_file_after_sha256"],
                "diff_sha256": record["diff_sha256"],
                "record": record["record_path"],
                "context_sha256": context["query"]["context_sha256"],
            },
            [record["record_path"], record["final_diff"]],
        )
        print(json.dumps(record, ensure_ascii=False, indent=2, default=str))
        return 0
    finally:
        connection.close()


def cmd_verify(args: argparse.Namespace) -> int:
    settings = _settings(args)
    baseline_path = settings.evidence_dir / "baseline" / "baseline.json"
    if not baseline_path.is_file():
        raise RuntimeError("baseline evidence is missing")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    connection = _connection(settings)
    try:
        client = _client(settings)
        context, context_path = _load_or_build_context(settings, args, connection, client)
        target_ids = test_node_ids(context, "target")
        regression_ids = test_node_ids(context, "regression")
        after_dir = settings.evidence_dir / "after"
        after_dir.mkdir(parents=True, exist_ok=True)
        target = run_pytest(settings.project_root, target_ids, after_dir / "target-tests.txt")
        regression = run_pytest(
            settings.project_root, regression_ids, after_dir / "regression-tests.txt"
        )
        full = run_pytest(settings.project_root, [], after_dir / "full-suite.txt")
        target_ok = target["exit_code"] == 0 and passed_count(target["output"]) == len(target_ids)
        regression_ok = (
            regression["exit_code"] == 0
            and passed_count(regression["output"]) == len(regression_ids)
        )
        full_ok = full["exit_code"] == 0
        test_hashes = test_file_hashes(settings.project_root)
        hashes_unchanged = test_hashes == baseline.get("test_file_hashes", {})
        target_set_unchanged = sorted(target_ids) == sorted(baseline.get("target_tests", []))
        regression_set_unchanged = sorted(regression_ids) == sorted(
            baseline.get("regression_tests", [])
        )
        target_paths = sorted({item["path"] for item in context["sources"]["code"]})
        diff = git_diff(settings.project_root, target_paths)
        record_path = settings.evidence_dir / "minimax" / "final" / "record.json"
        record = json.loads(record_path.read_text(encoding="utf-8")) if record_path.is_file() else {}
        correspondence = True
        correspondence_notes = []
        if record:
            current_hashes = {
                path: sha256_file(settings.project_root / path) for path in target_paths
            }
            if current_hashes.get(record["target_file"]) != record["target_file_after_sha256"]:
                correspondence = False
                correspondence_notes.append("current target file hash differs from MiniMax record")
            if record["diff_sha256"] != sha256_text(diff):
                correspondence = False
                correspondence_notes.append("current git diff differs from the MiniMax applied diff")
            if record["context_sha256"] != context["query"]["context_sha256"]:
                correspondence = False
                correspondence_notes.append("context hash differs from the MiniMax record")
        else:
            correspondence = False
            correspondence_notes.append("MiniMax record is missing")
        ok = (
            target_ok
            and regression_ok
            and full_ok
            and hashes_unchanged
            and target_set_unchanged
            and regression_set_unchanged
            and bool(diff.strip())
            and correspondence
        )
        diff_path = settings.evidence_dir / "diff" / "final.diff"
        diff_path.parent.mkdir(parents=True, exist_ok=True)
        diff_path.write_text(diff, encoding="utf-8", newline="\n")
        summary = {
            "schema": "sdd.verification.v1",
            "created_at": _timestamp(),
            "ok": ok,
            "context": _rel(context_path),
            "context_sha256": context["query"]["context_sha256"],
            "results": {
                "target": {
                    "command": target["command"],
                    "exit_code": target["exit_code"],
                    "passed": passed_count(target["output"]),
                    "failed": failed_count(target["output"]),
                    "log": _rel(after_dir / "target-tests.txt"),
                },
                "regression": {
                    "command": regression["command"],
                    "exit_code": regression["exit_code"],
                    "passed": passed_count(regression["output"]),
                    "failed": failed_count(regression["output"]),
                    "log": _rel(after_dir / "regression-tests.txt"),
                },
                "full": {
                    "command": full["command"],
                    "exit_code": full["exit_code"],
                    "passed": passed_count(full["output"]),
                    "failed": failed_count(full["output"]),
                    "log": _rel(after_dir / "full-suite.txt"),
                },
            },
            "checks": {
                "test_files_unchanged": hashes_unchanged,
                "target_set_unchanged": target_set_unchanged,
                "regression_set_unchanged": regression_set_unchanged,
                "diff_non_empty": bool(diff.strip()),
                "minimax_record_corresponds": correspondence,
                "correspondence_notes": correspondence_notes,
            },
            "test_file_hashes": test_hashes,
            "diff": _rel(diff_path),
            "diff_sha256": sha256_text(diff),
            "minimax_record": _rel(record_path) if record_path.is_file() else "",
        }
        _write_json(after_dir / "verify.json", summary)
        if args.record and ok:
            record_phase(
                client,
                context["state"]["scope_id"],
                context["task"]["task_id"],
                "validation",
                {
                    "target_exit_code": target["exit_code"],
                    "target_passed": passed_count(target["output"]),
                    "regression_exit_code": regression["exit_code"],
                    "regression_passed": passed_count(regression["output"]),
                    "full_exit_code": full["exit_code"],
                    "full_passed": passed_count(full["output"]),
                    "test_files_unchanged": hashes_unchanged,
                    "minimax_record_corresponds": correspondence,
                    "diff_sha256": sha256_text(diff),
                },
                [
                    _rel(after_dir / "verify.json"),
                    _rel(after_dir / "target-tests.txt"),
                    _rel(after_dir / "regression-tests.txt"),
                    _rel(after_dir / "full-suite.txt"),
                    _rel(diff_path),
                ],
            )
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 0 if ok else 1
    finally:
        connection.close()


def cmd_status(args: argparse.Namespace) -> int:
    settings = _settings(args)
    task = parse_task(args.task or _default_task(settings))
    client = _client(settings)
    scope_id = client.ensure_scope(settings.runtime_dir, task.task_id)
    entries = client.list_entries(scope_id)
    state = state_from_entries(entries, task.task_id)
    state["scope_id"] = scope_id
    state["provider"] = "PowerContext HTTP"
    state["search"] = client.search(scope_id, f"{task.task_id} progress validation", limit=10)
    state["prepared"] = client.prepare(
        scope_id, f"{task.task_id} progress validation", max_bytes=8000
    )
    print(json.dumps(state, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    if not args.yes:
        raise RuntimeError("reset drops the sdd_context tables; pass --yes to confirm")
    settings = _settings(args)
    ensure_database(settings)
    connection = connect(settings)
    try:
        reset_schema(connection)
    finally:
        connection.close()
    scope_file = settings.runtime_dir / "powercontext-scope.json"
    if scope_file.is_file():
        scope_file.unlink()
    print(
        json.dumps(
            {
                "ok": True,
                "dropped": True,
                "database": settings.seekdb_database,
                "scope_file_removed": str(scope_file),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    settings = _settings(args)
    report: dict = {
        "project": str(settings.project_root),
        "project_files": {
            "spec": (settings.project_root / "specs" / "reporting.md").is_file(),
            "trace": (settings.project_root / "specs" / "traceability.json").is_file(),
            "task": _default_task(settings).is_file(),
        },
        "seekdb": {
            "host": settings.seekdb_host,
            "port": settings.seekdb_port,
            "database": settings.seekdb_database,
            "password_set": bool(settings.seekdb_password),
        },
        "powercontext": {
            "url": settings.powercontext_url,
            "token_set": bool(settings.powercontext_token),
        },
        "minimax": {
            "base_url": settings.minimax_base_url,
            "model": settings.minimax_model,
            "api_key_set": bool(settings.minimax_api_key),
        },
    }
    try:
        connection = _connection(settings)
        try:
            report["seekdb"]["version"] = server_version(connection)
            report["seekdb"]["tables"] = list_tables(connection)
        finally:
            connection.close()
        report["seekdb"]["ok"] = True
    except Exception as error:
        report["seekdb"] = {**report["seekdb"], "ok": False, "error": str(error)}
    try:
        client = _client(settings)
        report["powercontext"]["health"] = client.health()
        report["powercontext"]["ok"] = True
    except Exception as error:
        report["powercontext"] = {**report["powercontext"], "ok": False, "error": str(error)}
    report["minimax"]["ok"] = bool(settings.minimax_api_key)
    if args.minimax:
        if not settings.minimax_api_key:
            report["minimax"]["check"] = "skipped: API key not set"
        else:
            try:
                report["minimax"]["check"] = MiniMaxClient(settings).check()
                report["minimax"]["ok"] = True
            except Exception as error:
                report["minimax"]["ok"] = False
                report["minimax"]["error"] = str(error)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


def cmd_spec_update(args: argparse.Namespace) -> int:
    settings = _settings(args)
    connection = _connection(settings)
    try:
        client = _client(settings)
        target = settings.project_root / "specs" / "reporting.md"
        updated = Path(args.updated_file).resolve()
        before_text = target.read_text(encoding="utf-8")
        after_text = updated.read_text(encoding="utf-8")
        if before_text == after_text:
            raise RuntimeError("updated spec is identical to the current spec")
        update_dir = settings.evidence_dir / "spec-update"
        update_dir.mkdir(parents=True, exist_ok=True)
        (update_dir / "reporting.before.md").write_text(before_text, encoding="utf-8", newline="\n")
        (update_dir / "reporting.updated.md").write_text(after_text, encoding="utf-8", newline="\n")
        before_context_path = Path(args.before_context) if args.before_context else settings.evidence_dir / "context" / "initial.json"
        before_context = load_context(before_context_path) if before_context_path.is_file() else None

        target.write_text(after_text, encoding="utf-8", newline="\n")
        summary = import_project(connection, settings.project_root)
        _write_json(settings.runtime_dir / "import-summary-after-update.json", summary)
        context = build_context(
            settings,
            args.task or _default_task(settings),
            connection,
            client,
        )
        after_json = update_dir / "after.json"
        after_md = update_dir / "after.md"
        _write_json(after_json, context)
        after_md.write_text(render_markdown(context), encoding="utf-8", newline="\n")

        def pick(specs: list[dict]) -> dict:
            return next((spec for spec in specs if spec["req_id"] == "REQ-REPORT-004"), {})

        before_spec = pick(before_context["sources"]["specs"]) if before_context else {}
        after_spec = pick(context["sources"]["specs"])
        comparison = {
            "schema": "sdd.spec-update.v1",
            "created_at": _timestamp(),
            "ok": bool(before_spec) and before_spec.get("sha256") != after_spec.get("sha256"),
            "before_context": _rel(before_context_path) if before_context else "",
            "after_context": _rel(after_json),
            "before_sha256": before_spec.get("sha256"),
            "after_sha256": after_spec.get("sha256"),
            "before_body": before_spec.get("body", ""),
            "after_body": after_spec.get("body", ""),
            "before_context_sha256": before_context["query"]["context_sha256"] if before_context else "",
            "after_context_sha256": context["query"]["context_sha256"],
            "matched_req_ids": context["query"]["matched_req_ids"],
            "sources_verified": context["validation"]["verified"],
            "spec_update_applied": True,
        }
        _write_json(update_dir / "summary.json", comparison)
        comparison["after_markdown"] = _rel(after_md)
        print(json.dumps(comparison, ensure_ascii=False, indent=2, default=str))
        return 0 if comparison["ok"] else 1
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sdd-context",
        description="Spec-driven development context manager backed by seekdb and PowerContext",
    )
    parser.add_argument("--project", help="sample project root (default: sample_ledger)")
    parser.add_argument("--env-file", help="environment file (default: minimax/.env)")
    parser.add_argument("--runtime", help="runtime state directory")
    parser.add_argument("--evidence", help="evidence output directory")
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="create the seekdb schema and PowerContext scope")
    init.add_argument("--task", help="task markdown file")
    init.set_defaults(func=cmd_init)

    import_command = commands.add_parser("import", help="import specs, code and tests into seekdb")
    import_command.set_defaults(func=cmd_import)

    context = commands.add_parser("context", help="build the task context from seekdb and PowerContext")
    context.add_argument("--task", help="task markdown file")
    context.add_argument("--output", help="output JSON path")
    context.add_argument("--record", action="store_true", help="record the context phase in PowerContext")
    context.set_defaults(func=cmd_context)

    baseline = commands.add_parser("baseline", help="run the pre-change tests and save evidence")
    baseline.add_argument("--context", help="context JSON path")
    baseline.add_argument("--task", help="task markdown file")
    baseline.add_argument("--record", action="store_true")
    baseline.set_defaults(func=cmd_baseline)

    assist_command = commands.add_parser("assist", help="ask MiniMax to implement the change")
    assist_command.add_argument("--context", help="context JSON path")
    assist_command.add_argument("--task", help="task markdown file")
    assist_command.add_argument("--attempts", type=int, default=2)
    assist_command.set_defaults(func=cmd_assist)

    verify = commands.add_parser("verify", help="run post-change tests and save evidence")
    verify.add_argument("--context", help="context JSON path")
    verify.add_argument("--task", help="task markdown file")
    verify.add_argument("--record", action="store_true")
    verify.set_defaults(func=cmd_verify)

    status = commands.add_parser("status", help="read task state from PowerContext")
    status.add_argument("--task", help="task markdown file")
    status.set_defaults(func=cmd_status)

    spec_update = commands.add_parser("spec-update", help="apply an updated spec and re-import it")
    spec_update.add_argument("--updated-file", required=True)
    spec_update.add_argument("--before-context", help="pre-update context JSON")
    spec_update.add_argument("--task", help="task markdown file")
    spec_update.set_defaults(func=cmd_spec_update)

    reset = commands.add_parser("reset", help="drop the context tables and forget the current scope")
    reset.add_argument("--yes", action="store_true")
    reset.set_defaults(func=cmd_reset)

    check = commands.add_parser("check", help="check database, PowerContext and MiniMax configuration")
    check.add_argument("--minimax", action="store_true", help="call MiniMax with a minimal prompt")
    check.set_defaults(func=cmd_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (PowerContextError, MiniMaxError, RuntimeError, ValueError, pymysql.MySQLError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
