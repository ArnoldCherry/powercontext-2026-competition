from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .db import server_version
from .parsing import TaskDoc, parse_task, sha256_text
from .powercontext import PowerContextClient, state_from_entries


def _jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class QueryTrace:
    def __init__(self) -> None:
        self.statements: list[dict] = []

    def run(self, cursor, sql: str, params: tuple | list | None = None):
        cursor.execute(sql, params or ())
        self.statements.append({"sql": sql.strip(), "params": _jsonable(list(params or ()))})
        return cursor.fetchall()


def _placeholders(values: list[str]) -> str:
    return ", ".join(["%s"] * len(values))


def _read_slice(path: Path, line_start: int, line_end: int) -> str:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    return "".join(lines[line_start - 1 : line_end]).rstrip("\n")


def _match_requirements(cursor, trace: QueryTrace, task: TaskDoc) -> dict[str, list[str]]:
    rows = trace.run(cursor, "SELECT req_id, keyword FROM sdd_spec_keywords ORDER BY req_id, keyword")
    haystack = task.text.casefold()
    matched: dict[str, list[str]] = {}
    for row in rows:
        keyword = str(row["keyword"])
        if keyword.casefold() in haystack:
            matched.setdefault(str(row["req_id"]), []).append(keyword)
    return matched


def _score(matched: dict[str, list[str]]) -> dict[str, int]:
    return {
        req_id: sum(len(keyword) for keyword in keywords) + 2 * len(keywords)
        for req_id, keywords in matched.items()
    }


def _fetch_specs(cursor, trace: QueryTrace, req_ids: list[str]) -> list[dict]:
    sql = f"""
        SELECT req_id, doc_path, title, body, source, line_start, line_end, sha256, imported_at
        FROM sdd_spec_entries
        WHERE req_id IN ({_placeholders(req_ids)})
        ORDER BY req_id
    """
    return trace.run(cursor, sql, req_ids)


def _fetch_links(cursor, trace: QueryTrace, req_ids: list[str]) -> list[dict]:
    sql = f"""
        SELECT req_id, target_kind, target_path, target_symbol, relation, rationale
        FROM sdd_spec_links
        WHERE req_id IN ({_placeholders(req_ids)})
        ORDER BY req_id, target_kind, target_path, target_symbol
    """
    return trace.run(cursor, sql, req_ids)


def _fetch_symbol(cursor, trace: QueryTrace, path: str, qualname: str) -> dict | None:
    rows = trace.run(
        cursor,
        """
        SELECT path, qualname, symbol_kind, line_start, line_end, signature, docstring, source, sha256
        FROM sdd_code_symbols
        WHERE path = %s AND qualname = %s
        """,
        (path, qualname),
    )
    return rows[0] if rows else None


def _fetch_test(cursor, trace: QueryTrace, path: str, name: str) -> dict | None:
    rows = trace.run(
        cursor,
        """
        SELECT path, name, line_start, line_end, markers, docstring, source, sha256
        FROM sdd_test_cases
        WHERE path = %s AND name = %s
        """,
        (path, name),
    )
    return rows[0] if rows else None


def _doc_hash(cursor, path: str, kind: str) -> str:
    cursor.execute("SELECT sha256 FROM sdd_documents WHERE path = %s AND kind = %s", (path, kind))
    row = cursor.fetchone()
    return str(row["sha256"]) if row else ""


def build_context(
    settings: Settings,
    task_path: str | Path,
    connection,
    client: PowerContextClient,
    include_state: bool = True,
) -> dict:
    task = parse_task(task_path)
    trace = QueryTrace()
    with connection.cursor() as cursor:
        matched = _match_requirements(cursor, trace, task)
        scores = _score(matched)
        req_ids = sorted(scores, key=lambda req_id: (-scores[req_id], req_id))
        if not req_ids:
            rows = trace.run(cursor, "SELECT req_id FROM sdd_spec_entries ORDER BY req_id")
            req_ids = [str(row["req_id"]) for row in rows][:5]
        req_ids = req_ids[:8]

        spec_rows = _fetch_specs(cursor, trace, req_ids)
        specs = []
        for row in spec_rows:
            specs.append(
                {
                    "req_id": row["req_id"],
                    "title": row["title"],
                    "path": row["doc_path"],
                    "line_start": row["line_start"],
                    "line_end": row["line_end"],
                    "sha256": row["sha256"],
                    "score": scores.get(str(row["req_id"]), 0),
                    "matched_keywords": matched.get(str(row["req_id"]), []),
                    "body": row["body"],
                    "source": row["source"],
                    "imported_at": row["imported_at"].isoformat() + "Z"
                    if isinstance(row["imported_at"], datetime)
                    else str(row["imported_at"]),
                }
            )

        specs.sort(key=lambda item: (-item["score"], item["req_id"]))
        links = _fetch_links(cursor, trace, req_ids)
        code_links: dict[tuple[str, str], set[str]] = {}
        target_tests: dict[tuple[str, str], set[str]] = {}
        regression_tests: dict[tuple[str, str], set[str]] = {}
        for link in links:
            key = (str(link["target_path"]), str(link["target_symbol"]))
            if link["target_kind"] == "code":
                code_links.setdefault(key, set()).add(str(link["req_id"]))
            elif link["relation"] == "verifies":
                target_tests.setdefault(key, set()).add(str(link["req_id"]))
            elif link["relation"] == "regression":
                regression_tests.setdefault(key, set()).add(str(link["req_id"]))

        code = []
        related_req_ids: set[str] = set()
        for (path, qualname), linked_reqs in sorted(code_links.items()):
            symbol = _fetch_symbol(cursor, trace, path, qualname)
            if symbol is None:
                continue
            related = trace.run(
                cursor,
                """
                SELECT DISTINCT req_id FROM sdd_spec_links
                WHERE target_kind = 'code' AND target_path = %s AND target_symbol = %s
                ORDER BY req_id
                """,
                (path, qualname),
            )
            related_ids = [str(row["req_id"]) for row in related]
            related_req_ids.update(related_ids)
            code.append(
                {
                    "path": symbol["path"],
                    "symbol": symbol["qualname"],
                    "kind": symbol["symbol_kind"],
                    "signature": symbol["signature"],
                    "line_start": symbol["line_start"],
                    "line_end": symbol["line_end"],
                    "sha256": symbol["sha256"],
                    "file_sha256": _doc_hash(cursor, str(symbol["path"]), "code"),
                    "docstring": symbol["docstring"],
                    "source": symbol["source"],
                    "req_ids": sorted(linked_reqs),
                    "related_req_ids": related_ids,
                }
            )

        if related_req_ids:
            related_links = _fetch_links(cursor, trace, sorted(related_req_ids))
            for link in related_links:
                if link["target_kind"] != "test" or link["relation"] != "regression":
                    continue
                key = (str(link["target_path"]), str(link["target_symbol"]))
                regression_tests.setdefault(key, set()).add(str(link["req_id"]))

        tests = []
        all_test_links = {**target_tests}
        for key, reqs in regression_tests.items():
            all_test_links.setdefault(key, set()).update(reqs)
        for (path, name), reqs in sorted(all_test_links.items()):
            test = _fetch_test(cursor, trace, path, name)
            if test is None:
                continue
            kind = "target" if key_in(target_tests, path, name) else "regression"
            tests.append(
                {
                    "path": test["path"],
                    "name": test["name"],
                    "kind": kind,
                    "line_start": test["line_start"],
                    "line_end": test["line_end"],
                    "sha256": test["sha256"],
                    "file_sha256": _doc_hash(cursor, str(test["path"]), "test"),
                    "docstring": test["docstring"],
                    "markers": json.loads(test["markers"]) if test["markers"] else [],
                    "source": test["source"],
                    "req_ids": sorted(reqs),
                }
            )

        database_documents = trace.run(
            cursor,
            "SELECT path, kind, sha256, imported_at FROM sdd_documents ORDER BY path, kind",
        )
    tests.sort(key=lambda item: (0 if item["kind"] == "target" else 1, item["path"], item["name"]))

    issues: list[str] = []
    document_hashes = {
        (str(row["path"]), str(row["kind"])): str(row["sha256"]) for row in database_documents
    }
    for spec in specs:
        expected = document_hashes.get((spec["path"], "spec"))
        actual = _file_hash(settings.project_root / spec["path"])
        if not expected or actual != expected:
            issues.append(f"spec file hash mismatch: {spec['path']}")
        source = _read_slice(settings.project_root / spec["path"], spec["line_start"], spec["line_end"])
        if source != spec["source"].rstrip("\n"):
            issues.append(f"spec line range mismatch: {spec['req_id']} {spec['path']}:{spec['line_start']}")
    for item in code:
        expected = document_hashes.get((item["path"], "code"))
        actual = _file_hash(settings.project_root / item["path"])
        if not expected or actual != expected:
            issues.append(f"code file hash mismatch: {item['path']}")
        source = _read_slice(settings.project_root / item["path"], item["line_start"], item["line_end"])
        if source != item["source"].rstrip("\n"):
            issues.append(f"code line range mismatch: {item['path']}::{item['symbol']}")
    for item in tests:
        expected = document_hashes.get((item["path"], "test"))
        actual = _file_hash(settings.project_root / item["path"])
        if not expected or actual != expected:
            issues.append(f"test file hash mismatch: {item['path']}")
        source = _read_slice(settings.project_root / item["path"], item["line_start"], item["line_end"])
        if source != item["source"].rstrip("\n"):
            issues.append(f"test line range mismatch: {item['path']}::{item['name']}")

    state: dict = {
        "provider": "PowerContext HTTP",
        "scope_id": "",
        "completed": [],
        "pending": [],
        "validation_results": [],
        "entries": [],
        "search": {},
        "prepared": {},
    }
    if include_state:
        scope_id = client.ensure_scope(settings.runtime_dir, task.task_id)
        entries = client.list_entries(scope_id)
        state = state_from_entries(entries, task.task_id)
        state.update(
            {
                "provider": "PowerContext HTTP",
                "scope_id": scope_id,
                "search": client.search(
                    scope_id, f"{task.task_id} progress validation", limit=10
                ),
                "prepared": client.prepare(
                    scope_id, f"{task.task_id} progress validation", max_bytes=8000
                ),
            }
        )

    result = {
        "schema": "sdd.context.v1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "task": {
            "task_id": task.task_id,
            "title": task.title,
            "body": task.body,
            "path": _relative(task.path, settings.project_root),
            "sha256": task.sha256,
        },
        "database": {
            "version": server_version(connection),
            "documents": [
                {
                    "path": row["path"],
                    "kind": row["kind"],
                    "sha256": row["sha256"],
                }
                for row in database_documents
            ],
        },
        "sources": {"specs": specs, "code": code, "tests": tests},
        "state": state,
        "validation": {
            "verified": not issues,
            "issues": issues,
            "test_file_hashes": sorted(
                {
                    item["path"]: item["file_sha256"]
                    for item in tests
                }.items()
            ),
        },
        "query": {
            "matched_req_ids": req_ids,
            "statements": trace.statements,
        },
    }
    core = json.dumps(
        {"task": result["task"], "sources": result["sources"], "state": result["state"]},
        ensure_ascii=False,
        sort_keys=True,
    )
    context_sha = hashlib.sha256(core.encode("utf-8")).hexdigest()
    result["query"]["context_sha256"] = context_sha

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO sdd_query_log
                (task_id, task_sha256, statements, params_json, matched_reqs, response_sha256, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                task.task_id,
                task.sha256,
                json.dumps(trace.statements, ensure_ascii=False),
                json.dumps([statement["params"] for statement in trace.statements], ensure_ascii=False),
                ",".join(req_ids),
                context_sha,
                datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0),
            ),
        )
    connection.commit()
    return result


def key_in(mapping: dict[tuple[str, str], set[str]], path: str, name: str) -> bool:
    return (path, name) in mapping


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_node_ids(context: dict, kind: str) -> list[str]:
    return [
        f"{test['path']}::{test['name']}"
        for test in context["sources"]["tests"]
        if test["kind"] == kind
    ]


def load_context(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != "sdd.context.v1":
        raise ValueError(f"{path}: unsupported context schema")
    return data


def render_markdown(context: dict) -> str:
    task = context["task"]
    lines = [
        f"# SDD context: {task['task_id']} {task['title']}",
        "",
        f"- Context SHA-256: `{context['query']['context_sha256']}`",
        f"- Database: {context['database']['version']}",
        f"- Sources verified: {context['validation']['verified']}",
        "",
        "## Specifications",
    ]
    for spec in context["sources"]["specs"]:
        lines.append(
            f"- {spec['req_id']} {spec['title']} - `{spec['path']}:{spec['line_start']}-{spec['line_end']}` "
            f"(score {spec['score']}, keywords: {', '.join(spec['matched_keywords'])})"
        )
    lines.extend(["", "## Code"])
    for code in context["sources"]["code"]:
        lines.append(f"- `{code['path']}::{code['symbol']}` - {code['signature']}")
    lines.extend(["", "## Tests"])
    for test in context["sources"]["tests"]:
        lines.append(
            f"- [{test['kind']}] `{test['path']}::{test['name']}` - reqs: {', '.join(test['req_ids'])}"
        )
    state = context["state"]
    lines.extend(
        [
            "",
            "## PowerContext state",
            f"- Scope: `{state.get('scope_id', '')}`",
            f"- Completed: {', '.join(state.get('completed', [])) or '(none)'}",
            f"- Pending: {', '.join(state.get('pending', [])) or '(none)'}",
        ]
    )
    for item in state.get("validation_results", []):
        lines.append(f"- {item['phase']}: {item['text'].splitlines()[0]}")
    if context["validation"]["issues"]:
        lines.extend(["", "## Validation issues"])
        lines.extend(f"- {issue}" for issue in context["validation"]["issues"])
    return "\n".join(lines) + "\n"
