from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from board.config import Settings

PREFERENCE_PREFIX = "[preference]"
PROGRESS_PREFIX = "[progress]"
SCOPE_IDEMPOTENCY_KEY = "ds06-competition-board-v1"
LOCAL_TIMEZONE = timezone(timedelta(hours=8))


class MemoryUnavailable(RuntimeError):
    pass


def _now_local() -> str:
    return datetime.now(LOCAL_TIMEZONE).replace(microsecond=0).isoformat()


class MemoryService:
    def __init__(self, settings: Settings, store: Any) -> None:
        self.settings = settings
        self.store = store
        self._scope_id: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        if self.settings.powercontext_token:
            return {"Authorization": f"Bearer {self.settings.powercontext_token}"}
        return {}

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            response = httpx.request(
                method,
                f"{self.settings.powercontext_url}{path}",
                headers=self.headers,
                json=payload,
                timeout=self.settings.request_timeout,
                trust_env=False,
            )
        except httpx.HTTPError as error:
            raise MemoryUnavailable(f"无法连接 PowerContext: {error}") from error
        if response.status_code >= 400:
            detail = response.text.strip().replace("\n", " ")[:240]
            raise MemoryUnavailable(f"PowerContext {method} {path} 返回 {response.status_code}: {detail}")
        if not response.content:
            return {}
        return response.json()

    def health(self) -> dict[str, Any]:
        try:
            payload = self._call("GET", "/health/ready")
        except MemoryUnavailable as error:
            return {"status": "offline", "url": self.settings.powercontext_url, "error": str(error)}
        return {
            "status": "online",
            "url": self.settings.powercontext_url,
            "ready": payload.get("status") == "ready",
            "access_mode": payload.get("checks", {}).get("access_mode"),
        }

    def _scope_exists(self, scope_id: str) -> bool:
        try:
            response = httpx.get(
                f"{self.settings.powercontext_url}/v1/scopes/{quote(scope_id, safe='')}",
                headers=self.headers,
                timeout=self.settings.request_timeout,
                trust_env=False,
            )
        except httpx.HTTPError as error:
            raise MemoryUnavailable(f"无法连接 PowerContext: {error}") from error
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        raise MemoryUnavailable(f"PowerContext 查询 Scope 返回 {response.status_code}: {response.text[:200]}")

    def ensure_scope(self) -> str:
        if self._scope_id:
            return self._scope_id
        scope_id = self.store.get_meta("powercontext_scope_id")
        if scope_id and self._scope_exists(scope_id):
            self._scope_id = scope_id
            return scope_id
        payload = self._call(
            "POST",
            "/v1/scopes",
            {
                "title": self.settings.scope_title,
                "summary": self.settings.scope_summary,
                "idempotency_key": SCOPE_IDEMPOTENCY_KEY,
            },
        )
        scope_id = payload["scope_id"]
        self.store.set_meta("powercontext_scope_id", scope_id)
        self._scope_id = scope_id
        return scope_id

    def remember(self, kind: str, text: str, reason: str) -> dict[str, Any]:
        scope_id = self.ensure_scope()
        return self._call(
            "POST",
            "/v1/memory/remember",
            {"scope_id": scope_id, "kind": kind, "text": text, "reason": reason},
        )

    def search(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        scope_id = self.ensure_scope()
        payload = self._call(
            "POST",
            "/v1/memory/search",
            {"scope_id": scope_id, "query": query, "mode": "fts"},
        )
        return list(payload.get("hits", []))[:limit]

    def save_preference(self, preferences: dict[str, Any]) -> dict[str, Any]:
        payload = dict(preferences)
        payload["saved_at"] = _now_local()
        text = f"{PREFERENCE_PREFIX} {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
        result = self.remember("preference", text, "看板筛选与展示偏好，供下次打开时复用")
        return {"preference": payload, "citation": result.get("entry", {}).get("citation")}

    def load_preference(self) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for hit in self.search("preference", limit=50):
            text = str(hit.get("text", ""))
            if not text.startswith(PREFERENCE_PREFIX):
                continue
            try:
                parsed = json.loads(text[len(PREFERENCE_PREFIX) :].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                parsed["_citation"] = hit.get("citation")
                candidates.append(parsed)
        if not candidates:
            return None
        candidates.sort(key=lambda item: str(item.get("saved_at", "")), reverse=True)
        return candidates[0]

    def record_progress(self, task: dict[str, Any], reason: str) -> dict[str, Any]:
        payload = {
            "task": task.get("id"),
            "status": task.get("status"),
            "title": task.get("title"),
            "next": task.get("next_action") or "",
            "at": _now_local(),
        }
        text = f"{PROGRESS_PREFIX} {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
        result = self.remember("progress", text, reason)
        return {"entry": payload, "citation": result.get("entry", {}).get("citation")}

    def recent_progress(self, limit: int = 12) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for hit in self.search("progress", limit=80):
            text = str(hit.get("text", ""))
            if not text.startswith(PROGRESS_PREFIX):
                continue
            try:
                parsed = json.loads(text[len(PROGRESS_PREFIX) :].strip())
            except json.JSONDecodeError:
                parsed = {"text": text[len(PROGRESS_PREFIX) :].strip()}
            if isinstance(parsed, dict):
                parsed["_citation"] = hit.get("citation")
                entries.append(parsed)
        entries.sort(key=lambda item: str(item.get("at", "")), reverse=True)
        return entries[:limit]
