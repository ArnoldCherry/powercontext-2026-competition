from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .db import ensure_schema, server_version
from .parsing import (
    ParsedProject,
    discover_project,
    sha256_text,
    sdd_markers,
)

VALID_RELATIONS = {"implements", "verifies", "regression"}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _validate_links(parsed: ParsedProject) -> list[dict]:
    specs = {spec.req_id for spec in parsed.specs}
    symbols = parsed.symbol_index()
    tests = parsed.test_index()
    links: list[dict] = []
    known: set[tuple[str, str, str, str]] = set()

    for req_id, requirement in parsed.trace["requirements"].items():
        if req_id not in specs:
            raise ValueError(f"trace references unknown requirement {req_id}")
        for link in requirement.get("links", []):
            kind = link.get("kind")
            if kind not in {"code", "test"}:
                raise ValueError(f"{req_id}: unsupported link kind {kind!r}")
            path = str(link.get("path", ""))
            relation = str(link.get("relation", ""))
            if relation not in VALID_RELATIONS:
                raise ValueError(f"{req_id}: unsupported relation {relation!r}")
            if kind == "code":
                symbol = str(link.get("symbol", ""))
                if (path, symbol) not in symbols:
                    raise ValueError(f"{req_id}: code link {path}::{symbol} does not exist")
                target = symbol
            else:
                target = str(link.get("name", ""))
                if (path, target) not in tests:
                    raise ValueError(f"{req_id}: test link {path}::{target} does not exist")
            key = (req_id, kind, path, target)
            if key in known:
                continue
            known.add(key)
            links.append(
                {
                    "req_id": req_id,
                    "target_kind": kind,
                    "target_path": path,
                    "target_symbol": target,
                    "relation": relation,
                    "rationale": str(link.get("rationale", "")),
                    "trace_path": "specs/traceability.json",
                }
            )

    for test in parsed.tests:
        for req_id, kind in sdd_markers(test.markers):
            if req_id not in specs:
                raise ValueError(f"{test.path}::{test.name}: marker references unknown {req_id}")
            relation = "verifies" if kind == "target" else "regression" if kind == "regression" else ""
            if not relation:
                raise ValueError(f"{test.path}::{test.name}: unknown marker kind {kind!r}")
            key = (req_id, "test", test.path, test.name)
            existing = next(
                (
                    link
                    for link in links
                    if (link["req_id"], link["target_kind"], link["target_path"], link["target_symbol"])
                    == key
                ),
                None,
            )
            if existing:
                if existing["relation"] != relation:
                    raise ValueError(
                        f"{test.path}::{test.name}: marker kind {kind!r} conflicts with "
                        f"trace relation {existing['relation']!r}"
                    )
                continue
            known.add(key)
            links.append(
                {
                    "req_id": req_id,
                    "target_kind": "test",
                    "target_path": test.path,
                    "target_symbol": test.name,
                    "relation": relation,
                    "rationale": "pytest sdd marker",
                    "trace_path": "specs/traceability.json",
                }
            )

    return links


def import_project(connection, project_root: str | Path) -> dict:
    parsed = discover_project(project_root)
    ensure_schema(connection)
    links = _validate_links(parsed)
    now = _now()
    manifest = json.dumps(
        {
            "documents": sorted(parsed.documents, key=lambda doc: (doc["kind"], doc["path"])),
            "specs": sorted(spec.req_id for spec in parsed.specs),
            "code": sorted((symbol.path, symbol.qualname) for symbol in parsed.code_symbols),
            "tests": sorted((test.path, test.name) for test in parsed.tests),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    manifest_sha = sha256_text(manifest)
    documents_json = json.dumps(parsed.documents, ensure_ascii=False, sort_keys=True)

    with connection.cursor() as cursor:
        for table in (
            "sdd_spec_keywords",
            "sdd_spec_links",
            "sdd_spec_entries",
            "sdd_code_symbols",
            "sdd_test_cases",
            "sdd_documents",
        ):
            cursor.execute(f"DELETE FROM {table}")

        for document in parsed.documents:
            cursor.execute(
                """
                INSERT INTO sdd_documents (path, kind, sha256, imported_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE sha256 = VALUES(sha256), imported_at = VALUES(imported_at)
                """,
                (document["path"], document["kind"], document["sha256"], now),
            )

        for spec in parsed.specs:
            cursor.execute(
                """
                INSERT INTO sdd_spec_entries
                    (doc_path, req_id, title, body, source, line_start, line_end, sha256, imported_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    spec.doc_path,
                    spec.req_id,
                    spec.title,
                    spec.body,
                    spec.source,
                    spec.line_start,
                    spec.line_end,
                    spec.sha256,
                    now,
                ),
            )

        for req_id, requirement in parsed.trace["requirements"].items():
            for keyword in requirement.get("keywords", []):
                keyword = str(keyword).strip()
                if not keyword:
                    continue
                cursor.execute(
                    """
                    INSERT INTO sdd_spec_keywords (req_id, keyword, doc_path)
                    VALUES (%s, %s, %s)
                    """,
                    (req_id, keyword, "specs/reporting.md"),
                )

        for link in links:
            cursor.execute(
                """
                INSERT INTO sdd_spec_links
                    (req_id, target_kind, target_path, target_symbol, relation, rationale, trace_path)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    link["req_id"],
                    link["target_kind"],
                    link["target_path"],
                    link["target_symbol"],
                    link["relation"],
                    link["rationale"],
                    link["trace_path"],
                ),
            )

        for symbol in parsed.code_symbols:
            cursor.execute(
                """
                INSERT INTO sdd_code_symbols
                    (path, qualname, symbol_kind, line_start, line_end, signature,
                     docstring, source, sha256)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    symbol.path,
                    symbol.qualname,
                    symbol.kind,
                    symbol.line_start,
                    symbol.line_end,
                    symbol.signature,
                    symbol.docstring,
                    symbol.source,
                    symbol.sha256,
                ),
            )

        for test in parsed.tests:
            cursor.execute(
                """
                INSERT INTO sdd_test_cases
                    (path, name, line_start, line_end, markers, docstring, source, sha256)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    test.path,
                    test.name,
                    test.line_start,
                    test.line_end,
                    json.dumps(test.markers, ensure_ascii=False, sort_keys=True),
                    test.docstring,
                    test.source,
                    test.sha256,
                ),
            )

        cursor.execute(
            """
            INSERT INTO sdd_import_runs
                (imported_at, manifest_sha256, documents_json, specs_count, code_count, tests_count)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                now,
                manifest_sha,
                documents_json,
                len(parsed.specs),
                len(parsed.code_symbols),
                len(parsed.tests),
            ),
        )
    connection.commit()

    target_tests = sorted(
        {
            f"{link['target_path']}::{link['target_symbol']}"
            for link in links
            if link["target_kind"] == "test" and link["relation"] == "verifies"
        }
    )
    regression_tests = sorted(
        {
            f"{link['target_path']}::{link['target_symbol']}"
            for link in links
            if link["target_kind"] == "test" and link["relation"] == "regression"
        }
    )
    return {
        "database": server_version(connection),
        "imported_at": now.isoformat() + "Z",
        "manifest_sha256": manifest_sha,
        "documents": parsed.documents,
        "specs": [
            {
                "req_id": spec.req_id,
                "title": spec.title,
                "path": spec.doc_path,
                "line_start": spec.line_start,
                "line_end": spec.line_end,
                "sha256": spec.sha256,
            }
            for spec in parsed.specs
        ],
        "code_symbols": [
            {"path": symbol.path, "symbol": symbol.qualname, "sha256": symbol.sha256}
            for symbol in parsed.code_symbols
        ],
        "tests": [
            {
                "path": test.path,
                "name": test.name,
                "markers": test.markers,
                "sha256": test.sha256,
            }
            for test in parsed.tests
        ],
        "links": links,
        "target_tests": target_tests,
        "regression_tests": regression_tests,
        "counts": {
            "specs": len(parsed.specs),
            "code_symbols": len(parsed.code_symbols),
            "tests": len(parsed.tests),
            "links": len(links),
        },
    }
