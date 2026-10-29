from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATUSES = ("todo", "doing", "blocked", "ready", "submitted", "scored")
DIFFICULTIES = ("S", "A", "B")
PRIORITIES = ("P0", "P1", "P2")

TASK_COLUMNS = (
    "id",
    "title",
    "direction",
    "difficulty",
    "status",
    "owner",
    "priority",
    "repo",
    "branch",
    "commit_hash",
    "submission_url",
    "task_number",
    "score",
    "next_action",
    "evidence_json",
    "notes",
    "created_at",
    "updated_at",
)

EDITABLE_FIELDS = (
    "title",
    "direction",
    "difficulty",
    "status",
    "owner",
    "priority",
    "repo",
    "branch",
    "commit_hash",
    "submission_url",
    "task_number",
    "score",
    "next_action",
    "evidence",
    "notes",
)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _init(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    difficulty TEXT NOT NULL,
                    status TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'P1',
                    repo TEXT NOT NULL DEFAULT '',
                    branch TEXT NOT NULL DEFAULT '',
                    commit_hash TEXT NOT NULL DEFAULT '',
                    submission_url TEXT NOT NULL DEFAULT '',
                    task_number TEXT NOT NULL DEFAULT '',
                    score REAL,
                    next_action TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT '[]',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def get_meta(self, key: str) -> str | None:
        with self._connection() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def clear_tasks(self) -> None:
        with self._connection() as connection:
            connection.execute("DELETE FROM tasks")

    def count_tasks(self) -> int:
        with self._connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> dict[str, Any]:
        task = dict(row)
        try:
            task["evidence"] = json.loads(task.pop("evidence_json") or "[]")
        except json.JSONDecodeError:
            task["evidence"] = []
        return task

    def list_tasks(
        self,
        *,
        direction: str | None = None,
        difficulty: str | None = None,
        status: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if direction and direction != "全部":
            clauses.append("direction = ?")
            values.append(direction)
        if difficulty and difficulty != "全部":
            clauses.append("difficulty = ?")
            values.append(difficulty)
        if status and status != "全部":
            clauses.append("status = ?")
            values.append(status)
        if query:
            like = f"%{query}%"
            clauses.append(
                "(id LIKE ? OR title LIKE ? OR owner LIKE ? OR next_action LIKE ? OR notes LIKE ? OR repo LIKE ?)"
            )
            values.extend([like] * 6)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection() as connection:
            rows = connection.execute(
                f"SELECT * FROM tasks {where} ORDER BY CASE status "
                "WHEN 'blocked' THEN 0 WHEN 'ready' THEN 1 WHEN 'doing' THEN 2 "
                "WHEN 'todo' THEN 3 WHEN 'submitted' THEN 4 ELSE 5 END, id",
                values,
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def create_task(self, values: dict[str, Any]) -> dict[str, Any]:
        task_id = str(values["id"]).strip()
        now = utc_now()
        record = {
            "id": task_id,
            "title": str(values.get("title", "")).strip(),
            "direction": str(values.get("direction", "未分类")).strip() or "未分类",
            "difficulty": str(values.get("difficulty", "B")).strip(),
            "status": str(values.get("status", "todo")).strip(),
            "owner": str(values.get("owner", "")).strip(),
            "priority": str(values.get("priority", "P1")).strip(),
            "repo": str(values.get("repo", "")).strip(),
            "branch": str(values.get("branch", "")).strip(),
            "commit_hash": str(values.get("commit_hash", "")).strip(),
            "submission_url": str(values.get("submission_url", "")).strip(),
            "task_number": str(values.get("task_number", "")).strip(),
            "score": values.get("score"),
            "next_action": str(values.get("next_action", "")).strip(),
            "evidence_json": json.dumps(values.get("evidence") or [], ensure_ascii=False),
            "notes": str(values.get("notes", "")).strip(),
            "created_at": now,
            "updated_at": now,
        }
        with self._connection() as connection:
            connection.execute(
                f"INSERT INTO tasks ({', '.join(TASK_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in TASK_COLUMNS)})",
                [record[column] for column in TASK_COLUMNS],
            )
        return self.get_task(task_id) or record

    def update_task(self, task_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        if not self.get_task(task_id):
            return None
        updates: dict[str, Any] = {}
        for field in EDITABLE_FIELDS:
            if field not in changes:
                continue
            value = changes[field]
            if field == "evidence":
                updates["evidence_json"] = json.dumps(value or [], ensure_ascii=False)
            elif field == "score":
                updates["score"] = value
            elif field in {"title", "direction", "owner", "repo", "branch", "commit_hash", "submission_url", "task_number", "next_action", "notes"}:
                updates[field] = str(value or "").strip()
            else:
                updates[field] = value
        if not updates:
            return self.get_task(task_id)
        updates["updated_at"] = utc_now()
        assignments = ", ".join(f"{column} = ?" for column in updates)
        with self._connection() as connection:
            connection.execute(
                f"UPDATE tasks SET {assignments} WHERE id = ?",
                [*updates.values(), task_id],
            )
        return self.get_task(task_id)

    def directions(self) -> list[str]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT DISTINCT direction FROM tasks WHERE direction <> '' ORDER BY direction"
            ).fetchall()
        return [row["direction"] for row in rows]

    def stats(self) -> dict[str, Any]:
        with self._connection() as connection:
            counts = {
                row["status"]: row["total"]
                for row in connection.execute("SELECT status, COUNT(*) AS total FROM tasks GROUP BY status")
            }
            score_row = connection.execute(
                "SELECT COALESCE(SUM(score), 0) AS total, COUNT(score) AS scored FROM tasks"
            ).fetchone()
        total = sum(counts.values())
        done = counts.get("submitted", 0) + counts.get("scored", 0)
        payload: dict[str, Any] = {
            "total": total,
            "done": done,
            "completion_rate": round(done / total * 100, 1) if total else 0.0,
            "score_sum": round(float(score_row["total"]), 2),
            "score_count": int(score_row["scored"]),
        }
        payload.update({status: counts.get(status, 0) for status in STATUSES})
        return payload
