from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

PHASES = ("context", "baseline", "patch", "validation")


class PowerContextError(RuntimeError):
    pass


class PowerContextClient:
    def __init__(self, base_url: str, token: str = "", timeout: int = 15):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body else {}
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise PowerContextError(
                f"PowerContext {method} {path} failed with HTTP {error.code}: {detail[:1200]}"
            ) from error
        except URLError as error:
            raise PowerContextError(
                f"PowerContext is not reachable at {self.base_url}: {error.reason}"
            ) from error

    def health(self) -> dict:
        return self._request("GET", "/health/live")

    def create_scope(self, title: str, summary: str, idempotency_key: str) -> dict:
        return self._request(
            "POST",
            "/v1/scopes",
            {"title": title, "summary": summary, "idempotency_key": idempotency_key},
        )

    def get_scope(self, scope_id: str) -> dict | None:
        try:
            return self._request("GET", f"/v1/scopes/{quote(scope_id, safe='')}")
        except PowerContextError as error:
            if "HTTP 404" in str(error):
                return None
            raise

    def ensure_scope(self, runtime_dir: Path, task_id: str) -> str:
        runtime_dir.mkdir(parents=True, exist_ok=True)
        state_file = runtime_dir / "powercontext-scope.json"
        if state_file.is_file():
            state = json.loads(state_file.read_text(encoding="utf-8"))
            scope_id = state.get("scope_id")
            if scope_id and self.get_scope(scope_id):
                return str(scope_id)
        created = self.create_scope(
            title="SDD MiniMax ledger demo",
            summary=f"Spec-driven context and task state for {task_id}",
            idempotency_key=f"sdd-{task_id}-{uuid.uuid4().hex[:12]}",
        )
        scope_id = str(created["scope_id"])
        state_file.write_text(
            json.dumps({"scope_id": scope_id, "task_id": task_id, "created_at": _timestamp()}),
            encoding="utf-8",
        )
        return scope_id

    def remember(self, scope_id: str, kind: str, text: str, reason: str = "") -> dict:
        return self._request(
            "POST",
            "/v1/memory/remember",
            {
                "scope_id": scope_id,
                "kind": kind,
                "text": text[:8192],
                "reason": reason[:512] or None,
            },
        )

    def capture_source(
        self, scope_id: str, source_id: str, content: str, metadata: dict | None = None
    ) -> dict:
        return self._request(
            "POST",
            "/v1/sources/content",
            {
                "scope_id": scope_id,
                "source_id": source_id,
                "content": content[:200000],
                "metadata": metadata or {},
            },
        )

    def list_entries(self, scope_id: str) -> list[dict]:
        result = self._request("POST", "/v1/memory/entries/list", {"scope_id": scope_id})
        return list(result.get("entries", []))

    def search(self, scope_id: str, query: str, limit: int = 10) -> dict:
        return self._request(
            "POST",
            "/v1/memory/search",
            {"scope_id": scope_id, "query": query, "limit": limit},
        )

    def prepare(self, scope_id: str, query: str, max_bytes: int = 8000) -> dict:
        return self._request(
            "POST",
            "/v1/context/prepare",
            {"scope_id": scope_id, "query": query, "max_bytes": max_bytes},
        )


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def record_phase(
    client: PowerContextClient,
    scope_id: str,
    task_id: str,
    phase: str,
    summary: dict,
    evidence_paths: list[str] | None = None,
) -> dict:
    if phase not in PHASES:
        raise ValueError(f"unknown phase {phase!r}; expected one of {PHASES}")
    evidence_paths = evidence_paths or []
    now = _timestamp()
    source_id = f"{task_id}-{phase}-{uuid.uuid4().hex[:10]}"
    detail = {
        "task_id": task_id,
        "phase": phase,
        "recorded_at": now,
        "summary": summary,
        "evidence": evidence_paths,
    }
    content = json.dumps(detail, ensure_ascii=False, indent=2, sort_keys=True)
    source = client.capture_source(
        scope_id,
        source_id=source_id,
        content=content,
        metadata={"task_id": task_id, "phase": phase, "kind": "sdd-phase-record"},
    )

    lines = [
        f"[{task_id}] phase={phase} recorded_at={now}",
        f"summary: {json.dumps(summary, ensure_ascii=False, sort_keys=True)}",
        f"powercontext_source: {source_id}",
    ]
    if evidence_paths:
        lines.append("evidence: " + ", ".join(evidence_paths))
    memory = client.remember(
        scope_id,
        kind=f"sdd-{phase}",
        text="\n".join(lines)[:8192],
        reason=f"SDD task {task_id} {phase} record",
    )
    return {"source_id": source_id, "source": source, "memory": memory}


def state_from_entries(entries: list[dict], task_id: str) -> dict:
    prefix = f"[{task_id}]"
    relevant = [entry for entry in entries if prefix in str(entry.get("text", ""))]
    completed: list[str] = []
    results: list[dict] = []
    for entry in relevant:
        text = str(entry.get("text", ""))
        phase = ""
        for part in text.splitlines()[0].split():
            if part.startswith("phase="):
                phase = part.split("=", 1)[1]
                break
        if phase and phase not in completed:
            completed.append(phase)
        if phase in {"baseline", "validation"}:
            results.append(
                {
                    "kind": entry.get("kind"),
                    "phase": phase,
                    "text": text,
                    "citation": entry.get("citation"),
                }
            )
    ordered = [phase for phase in PHASES if phase in completed]
    pending = [phase for phase in PHASES if phase not in completed]
    return {
        "task_id": task_id,
        "completed": ordered,
        "pending": pending,
        "validation_results": results,
        "entries": relevant,
    }
