#!/usr/bin/env python3
"""Export seekdb schema counts and the context query log as evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sdd_context.config import load_settings  # noqa: E402
from sdd_context.db import connect, list_tables, server_version  # noqa: E402


def main() -> int:
    settings = load_settings()
    connection = connect(settings)
    try:
        tables = list_tables(connection)
        with connection.cursor() as cursor:
            counts = {}
            for table in tables:
                cursor.execute(f"SELECT COUNT(*) AS count FROM {table}")
                counts[table] = int(cursor.fetchone()["count"])
            cursor.execute(
                """
                SELECT id, task_id, task_sha256, statements, params_json, matched_reqs,
                       response_sha256, created_at
                FROM sdd_query_log ORDER BY id
                """
            )
            rows = cursor.fetchall()
        queries = []
        for row in rows:
            queries.append(
                {
                    "id": row["id"],
                    "task_id": row["task_id"],
                    "task_sha256": row["task_sha256"],
                    "matched_reqs": row["matched_reqs"],
                    "response_sha256": row["response_sha256"],
                    "created_at": str(row["created_at"]),
                    "statements": json.loads(row["statements"]),
                    "params": json.loads(row["params_json"]),
                }
            )
        payload = {
            "schema": "sdd.database-evidence.v1",
            "database": {
                "host": settings.seekdb_host,
                "port": settings.seekdb_port,
                "name": settings.seekdb_database,
                "version": server_version(connection),
                "tables": tables,
                "counts": counts,
            },
            "query_log": queries,
        }
        output = settings.evidence_dir / "database" / "query-log.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "output": str(output),
                    "version": payload["database"]["version"],
                    "tables": len(tables),
                    "queries": len(queries),
                    "counts": counts,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
