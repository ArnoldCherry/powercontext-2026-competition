from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from board.config import load_settings
from board.memory import MemoryService, MemoryUnavailable
from board.store import DIFFICULTIES, STATUSES, Store

settings = load_settings()
store = Store(settings.db_path)
memory = MemoryService(settings, store)

STATIC_DIR = Path(__file__).resolve().parent / "static"
LOCAL_TIMEZONE = timezone(timedelta(hours=8))
DEFAULT_PREFERENCES = {
    "board_title": "生态赛交付看板",
    "default_direction": "全部",
    "default_status": "全部",
    "default_difficulty": "全部",
}

Status = Literal["todo", "doing", "blocked", "ready", "submitted", "scored"]
Difficulty = Literal["S", "A", "B"]
Priority = Literal["P0", "P1", "P2"]


class EvidenceLink(BaseModel):
    label: str = Field(default="", max_length=80)
    url: str = Field(min_length=1, max_length=2000)

    @field_validator("url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        return value.strip()


class TaskCreate(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
    title: str = Field(min_length=1, max_length=160)
    direction: str = Field(default="未分类", max_length=40)
    difficulty: Difficulty = "B"
    status: Status = "todo"
    owner: str = Field(default="", max_length=40)
    priority: Priority = "P1"
    repo: str = Field(default="", max_length=300)
    branch: str = Field(default="", max_length=200)
    commit_hash: str = Field(default="", max_length=80)
    submission_url: str = Field(default="", max_length=2000)
    task_number: str = Field(default="", max_length=80)
    score: float | None = Field(default=None, ge=0, le=100)
    next_action: str = Field(default="", max_length=500)
    evidence: list[EvidenceLink] = Field(default_factory=list, max_length=20)
    notes: str = Field(default="", max_length=4000)


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    direction: str | None = Field(default=None, max_length=40)
    difficulty: Difficulty | None = None
    status: Status | None = None
    owner: str | None = Field(default=None, max_length=40)
    priority: Priority | None = None
    repo: str | None = Field(default=None, max_length=300)
    branch: str | None = Field(default=None, max_length=200)
    commit_hash: str | None = Field(default=None, max_length=80)
    submission_url: str | None = Field(default=None, max_length=2000)
    task_number: str | None = Field(default=None, max_length=80)
    score: float | None = Field(default=None, ge=0, le=100)
    next_action: str | None = Field(default=None, max_length=500)
    evidence: list[EvidenceLink] | None = Field(default=None, max_length=20)
    notes: str | None = Field(default=None, max_length=4000)


class PreferencePayload(BaseModel):
    board_title: str = Field(default="生态赛交付看板", min_length=1, max_length=60)
    default_direction: str = Field(default="全部", max_length=40)
    default_status: str = Field(default="全部", max_length=20)
    default_difficulty: str = Field(default="全部", max_length=10)

    @field_validator("default_status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value != "全部" and value not in STATUSES:
            raise ValueError("非法状态")
        return value

    @field_validator("default_difficulty")
    @classmethod
    def validate_difficulty(cls, value: str) -> str:
        if value != "全部" and value not in DIFFICULTIES:
            raise ValueError("非法难度")
        return value


app = FastAPI(title="生态赛交付看板", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _local_now() -> str:
    return datetime.now(LOCAL_TIMEZONE).replace(microsecond=0).isoformat()


def _merged_preferences(saved: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_PREFERENCES)
    if saved:
        for key in DEFAULT_PREFERENCES:
            value = saved.get(key)
            if isinstance(value, str) and value.strip():
                merged[key] = value.strip()
        for key in ("saved_at", "version"):
            if saved.get(key) is not None:
                merged[key] = saved[key]
    return merged


def _memory_bundle() -> dict[str, Any]:
    health = memory.health()
    bundle: dict[str, Any] = {
        "memory": health,
        "preferences": dict(DEFAULT_PREFERENCES),
        "preference_source": "defaults",
        "preference_saved_at": None,
        "progress": [],
        "scope_id": store.get_meta("powercontext_scope_id"),
    }
    if health["status"] != "online":
        return bundle
    try:
        scope_id = memory.ensure_scope()
        bundle["scope_id"] = scope_id
        bundle["memory"] = {**health, "scope_id": scope_id}
        saved = memory.load_preference()
        if saved:
            bundle["preferences"] = _merged_preferences(saved)
            bundle["preference_source"] = "powercontext"
            bundle["preference_saved_at"] = saved.get("saved_at")
        bundle["progress"] = memory.recent_progress()
    except MemoryUnavailable as error:
        bundle["memory"] = {**health, "status": "error", "error": str(error)}
    return bundle


def _memory_write(action: Any) -> dict[str, Any]:
    try:
        return {"status": "saved", "detail": action()}
    except MemoryUnavailable as error:
        return {"status": "error", "error": str(error)}


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    memory_health = memory.health()
    return {
        "status": "ok",
        "database": {"status": "ok", "tasks": store.count_tasks(), "path": str(settings.db_path)},
        "memory": memory_health,
        "scope_id": store.get_meta("powercontext_scope_id"),
        "time": _local_now(),
    }


@app.get("/api/bootstrap")
def bootstrap(
    direction: str | None = None,
    difficulty: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    bundle = _memory_bundle()
    return {
        "tasks": store.list_tasks(direction=direction, difficulty=difficulty, status=status, query=q),
        "stats": store.stats(),
        "directions": store.directions(),
        "generated_at": _local_now(),
        **bundle,
    }


@app.get("/api/tasks")
def list_tasks(
    direction: str | None = None,
    difficulty: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    tasks = store.list_tasks(direction=direction, difficulty=difficulty, status=status, query=q)
    return {"items": tasks, "total": len(tasks)}


@app.post("/api/tasks", status_code=201)
def create_task(payload: TaskCreate) -> dict[str, Any]:
    if store.get_task(payload.id):
        raise HTTPException(status_code=409, detail=f"任务 {payload.id} 已存在")
    task = store.create_task(payload.model_dump())
    memory_result = _memory_write(lambda: memory.record_progress(task, f"创建任务 {task['id']}"))
    return {"task": task, "memory": memory_result}


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: str, payload: TaskUpdate) -> dict[str, Any]:
    before = store.get_task(task_id)
    if not before:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return {"task": before, "memory": {"status": "skipped", "reason": "没有字段变化"}}
    task = store.update_task(task_id, changes)
    memory_fields = {"status", "next_action", "evidence", "submission_url", "task_number", "score", "commit_hash"}
    if memory_fields.intersection(changes):
        memory_result = _memory_write(
            lambda: memory.record_progress(task, f"更新任务 {task_id}: {before['status']} -> {task['status']}")
        )
    else:
        memory_result = {"status": "skipped", "reason": "本次修改仅涉及描述字段"}
    return {"task": task, "previous_status": before["status"], "memory": memory_result}


@app.get("/api/preferences")
def get_preferences() -> dict[str, Any]:
    bundle = _memory_bundle()
    return {
        "preferences": bundle["preferences"],
        "source": bundle["preference_source"],
        "saved_at": bundle["preference_saved_at"],
        "memory": bundle["memory"],
    }


@app.put("/api/preferences")
def save_preferences(payload: PreferencePayload) -> dict[str, Any]:
    try:
        result = memory.save_preference(payload.model_dump())
    except MemoryUnavailable as error:
        raise HTTPException(status_code=503, detail=f"无法写入 PowerContext 记忆: {error}") from error
    return {"status": "saved", "preferences": payload.model_dump(), **result}


@app.get("/api/memory/progress")
def memory_progress(limit: int = Query(default=12, ge=1, le=50)) -> dict[str, Any]:
    try:
        entries = memory.recent_progress(limit=limit)
    except MemoryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"items": entries, "total": len(entries)}


@app.get("/api/memory/search")
def memory_search(q: str = Query(min_length=1, max_length=100), limit: int = Query(default=20, ge=1, le=80)):
    try:
        hits = memory.search(q, limit=limit)
    except MemoryUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return {"query": q, "hits": hits, "total": len(hits)}


if __name__ == "__main__":
    uvicorn.run(
        "board.app:app",
        host=os.environ.get("BOARD_HOST", "127.0.0.1"),
        port=int(os.environ.get("BOARD_PORT", "18800")),
        reload=False,
    )
