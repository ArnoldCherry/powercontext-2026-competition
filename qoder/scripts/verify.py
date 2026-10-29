from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import argparse
import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL_TIMEZONE = timezone(timedelta(hours=8))
STATE_PATH = PROJECT_ROOT / "data" / "verify_state.json"
EVIDENCE_DIR = PROJECT_ROOT / "evidence"


def now_local() -> str:
    return datetime.now(LOCAL_TIMEZONE).replace(microsecond=0).isoformat()


def request(client: httpx.Client, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.request(method, path, json=payload)
    try:
        body = response.json()
    except json.JSONDecodeError:
        body = {"raw": response.text}
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {response.status_code}: {body}")
    return body


def check(checks: list[dict[str, Any]], name: str, condition: bool, detail: Any) -> None:
    checks.append({"name": name, "ok": bool(condition), "detail": detail})
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}")


def find_task(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any] | None:
    return next((task for task in tasks if task["id"] == task_id), None)


def run(client: httpx.Client) -> int:
    checks: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {"phase": "write_and_read", "started_at": now_local(), "checks": checks}

    health = request(client, "GET", "/api/health")
    check(checks, "看板数据库可用", health["database"]["status"] == "ok", health["database"])
    check(checks, "PowerContext 在线", health["memory"]["status"] == "online", health["memory"])
    runtime = health.get("runtime") or {}
    check(
        checks,
        "PowerContext 由应用自动管理",
        runtime.get("mode") == "managed" and runtime.get("managed") is True and bool(runtime.get("pid")),
        runtime,
    )
    check(
        checks,
        "启动无错误且自动播种完成",
        not health.get("startup_error") and not health.get("seed_error"),
        {"startup_error": health.get("startup_error"), "seed_error": health.get("seed_error"), "seed": health.get("seed")},
    )

    bootstrap = request(client, "GET", "/api/bootstrap")
    check(checks, "已加载种子任务", len(bootstrap["tasks"]) >= 10, {"tasks": len(bootstrap["tasks"])})
    check(
        checks,
        "启动时从 PowerContext 读取偏好",
        bootstrap["preference_source"] == "powercontext",
        {
            "source": bootstrap["preference_source"],
            "preferences": bootstrap["preferences"],
            "saved_at": bootstrap.get("preference_saved_at"),
        },
    )
    check(checks, "启动时读取进展记忆", len(bootstrap["progress"]) >= 1, {"progress": bootstrap["progress"][:3]})
    check(checks, "看板已绑定 Scope", bool(bootstrap.get("scope_id")), {"scope_id": bootstrap.get("scope_id")})

    original = request(client, "GET", "/api/preferences")
    original_preferences = {
        key: original["preferences"][key]
        for key in ("board_title", "default_direction", "default_status", "default_difficulty")
    }
    temporary_preferences = {
        "board_title": "生态赛交付看板 · 记忆验证",
        "default_direction": "Qoder",
        "default_status": "ready",
        "default_difficulty": "A",
    }
    saved = request(client, "PUT", "/api/preferences", temporary_preferences)
    check(checks, "偏好写入 PowerContext", saved["status"] == "saved", saved.get("citation"))

    reloaded = request(client, "GET", "/api/bootstrap")
    reused = {key: reloaded["preferences"].get(key) for key in temporary_preferences}
    check(
        checks,
        "重新加载后复用偏好",
        reused == temporary_preferences and reloaded["preference_source"] == "powercontext",
        {"preferences": reloaded["preferences"], "source": reloaded["preference_source"]},
    )

    restored = request(client, "PUT", "/api/preferences", original_preferences)
    check(checks, "恢复原始偏好", restored["status"] == "saved", restored.get("citation"))

    run_id = f"verifyrun{secrets.token_hex(4)}"
    tasks_before = request(client, "GET", "/api/tasks")
    demo = find_task(tasks_before["items"], "DEMO-01")
    if demo is None:
        raise RuntimeError("未找到 DEMO-01，请先运行 scripts/seed.py")
    next_action = f"运行验证 {run_id} 并确认重启后可恢复"
    update = request(
        client,
        "PATCH",
        "/api/tasks/DEMO-01",
        {
            "status": "ready",
            "next_action": next_action,
            "evidence": [{"label": "验证记录", "url": f"https://example.com/verify/{run_id}"}],
        },
    )
    check(checks, "任务状态更新成功", update["task"]["status"] == "ready", update["task"])
    check(checks, "状态变更写入 PowerContext", update["memory"]["status"] == "saved", update["memory"])

    search = request(client, "GET", f"/api/memory/search?q={run_id}")
    check(checks, "可按 run_id 检索进展记忆", search["total"] >= 1, search["hits"][:2])

    refreshed = request(client, "GET", "/api/bootstrap")
    demo_after = find_task(refreshed["tasks"], "DEMO-01")
    check(
        checks,
        "刷新后任务与进展仍可见",
        demo_after is not None
        and demo_after["status"] == "ready"
        and demo_after["next_action"] == next_action
        and any(item.get("task") == "DEMO-01" and run_id in str(item.get("next", "")) for item in refreshed["progress"]),
        {"task": demo_after, "progress": refreshed["progress"][:5]},
    )

    state = {
        "run_id": run_id,
        "task_id": "DEMO-01",
        "expected_status": "ready",
        "expected_next_action": next_action,
        "expected_preferences": original_preferences,
        "created_at": now_local(),
    }
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    check(checks, "已保存重启验证状态", True, {"path": str(STATE_PATH), "run_id": run_id})

    evidence["finished_at"] = now_local()
    evidence["run_id"] = run_id
    evidence["powercontext_citation"] = update["memory"].get("detail", {}).get("citation")
    evidence["search_hits"] = search["hits"][:3]
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    output = EVIDENCE_DIR / "verification_write.json"
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"证据: {output}")
    return 0 if all(item["ok"] for item in checks) else 1


def run_after_restart(client: httpx.Client) -> int:
    checks: list[dict[str, Any]] = []
    evidence: dict[str, Any] = {"phase": "after_restart", "started_at": now_local(), "checks": checks}
    if not STATE_PATH.exists():
        print("缺少 data/verify_state.json，请先运行不带 --after-restart 的验证。")
        return 2
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))

    health = request(client, "GET", "/api/health")
    check(checks, "重启后看板数据库可用", health["database"]["status"] == "ok", health["database"])
    check(checks, "重启后 PowerContext 在线", health["memory"]["status"] == "online", health["memory"])
    runtime = health.get("runtime") or {}
    check(
        checks,
        "重启后仍由应用自动管理 PowerContext",
        runtime.get("mode") == "managed" and runtime.get("managed") is True and bool(runtime.get("pid")),
        runtime,
    )
    check(
        checks,
        "重启后无启动/播种错误",
        not health.get("startup_error") and not health.get("seed_error"),
        {"startup_error": health.get("startup_error"), "seed_error": health.get("seed_error")},
    )

    bootstrap = request(client, "GET", "/api/bootstrap")
    task = find_task(bootstrap["tasks"], state["task_id"])
    check(
        checks,
        "重启后任务状态与下一步仍在",
        task is not None
        and task["status"] == state["expected_status"]
        and task["next_action"] == state["expected_next_action"],
        task,
    )

    preferences = request(client, "GET", "/api/preferences")
    check(
        checks,
        "重启后偏好仍可恢复",
        preferences["source"] == "powercontext"
        and all(preferences["preferences"][key] == value for key, value in state["expected_preferences"].items()),
        preferences,
    )

    search = request(client, "GET", f"/api/memory/search?q={state['run_id']}")
    check(checks, "重启后仍能检索到进展记忆", search["total"] >= 1, search["hits"][:2])
    progress = request(client, "GET", "/api/memory/progress?limit=50")
    check(
        checks,
        "重启后进展列表仍包含本次验证",
        any(state["run_id"] in str(item.get("next", "")) for item in progress["items"]),
        progress["items"][:5],
    )

    evidence["finished_at"] = now_local()
    evidence["run_id"] = state["run_id"]
    evidence["search_hits"] = search["hits"][:3]
    evidence["preferences"] = preferences
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    output = EVIDENCE_DIR / "verification_after_restart.json"
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"证据: {output}")
    return 0 if all(item["ok"] for item in checks) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="验证看板与 PowerContext 的偏好、进展读写")
    parser.add_argument("--base-url", default="http://127.0.0.1:18800")
    parser.add_argument("--after-restart", action="store_true", help="重启 PowerContext 与看板后执行恢复验证")
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=20, trust_env=False) as client:
        if args.after_restart:
            return run_after_restart(client)
        return run(client)


if __name__ == "__main__":
    raise SystemExit(main())
