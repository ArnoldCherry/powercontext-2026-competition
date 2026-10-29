"""End-to-end security and idempotency checks for the Dify inn backend.

Run against a live backend with the same tokens configured in docker/.env:

    INN_SERVICE_TOKEN=... INN_ADMIN_TOKEN=... python3 tests/security_regression.py

Set ``INN_BASE_URL_2`` to a second backend process sharing the same seekdb to
exercise cross-process row locking.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.getenv("INN_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
BASE_2 = os.getenv("INN_BASE_URL_2", "").rstrip("/")
SERVICE = os.getenv("INN_SERVICE_TOKEN", "")
ADMIN = os.getenv("INN_ADMIN_TOKEN", "")


def call(base, path, data=None, headers=None, method=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = urllib.request.Request(
        base + path,
        data=body,
        headers=headers or {},
        method=method or ("POST" if data is not None else "GET"),
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            try:
                return response.status, json.loads(raw)
            except json.JSONDecodeError:
                return response.status, raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, raw.decode("utf-8", "replace")


def headers(player=None, request=None, admin=False):
    result = {"X-Inn-Service-Token": SERVICE}
    if player:
        result["X-Inn-Player-Id"] = player
    if request:
        result["X-Inn-Request-Id"] = request
    if admin:
        result["X-Admin-Token"] = ADMIN
        result["X-Inn-Admin-Token"] = ADMIN
    return result


def check(name, condition, detail=""):
    print(f"[{'PASS' if condition else 'FAIL'}] {name}: {detail}")
    return condition


def reset(base, player):
    return call(base, f"/api/players/{player}/reset", headers=headers(admin=True), method="POST")


def ledger(base, player, owner=True, admin=False):
    return call(
        base,
        f"/api/ledger/{player}",
        headers=headers(player if owner else "someone-else", admin=admin),
    )


def main() -> int:
    if not SERVICE or not ADMIN:
        print("INN_SERVICE_TOKEN and INN_ADMIN_TOKEN must be set", file=sys.stderr)
        return 2
    ok = True
    player = "reg-" + uuid.uuid4().hex
    other = "reg-" + uuid.uuid4().hex

    status, _ = reset(BASE, player)
    ok &= check("管理员 reset 可用", status == 200, str(status))
    status, _ = call(BASE, "/health")
    ok &= check("health 无需鉴权", status == 200, str(status))

    status, _ = call(BASE, "/api/daily")
    ok &= check("daily 缺服务 token 拒绝", status == 401, str(status))
    status, daily = call(BASE, "/api/daily", headers={"X-Inn-Service-Token": SERVICE})
    ok &= check("daily 带服务 token 可用", status == 200 and "passphrase" in daily, str(status))

    payload = {
        "utterance": '我叫阿飞，他说“来碗面”，\n第二行 🍜。',
        "conversation_id": "c1",
        "action": "点菜",
        "keywords": "断肠面",
        "player_name": "阿飞",
    }
    status, _ = call(BASE, "/api/visit", payload, headers={"X-Inn-Player-Id": player, "X-Inn-Request-Id": "r0"})
    ok &= check("visit 缺服务 token 拒绝", status == 401, str(status))
    status, _ = call(
        BASE,
        "/api/visit",
        payload,
        headers={"X-Inn-Service-Token": "wrong", "X-Inn-Player-Id": player, "X-Inn-Request-Id": "r0"},
    )
    ok &= check("visit 错误服务 token 拒绝", status == 401, str(status))
    status, _ = call(
        BASE,
        "/api/visit",
        payload,
        headers={"X-Inn-Service-Token": SERVICE, "X-Inn-Request-Id": "r0"},
    )
    ok &= check("visit 缺玩家 header 拒绝", status == 401, str(status))

    forged = {**payload, "player_id": other}
    status, _ = call(BASE, "/api/visit", forged, headers=headers(player, "r-forged"))
    ok &= check("伪造 body player_id 拒绝", status == 403, str(status))
    status, _ = ledger(BASE, player)
    ok &= check("拒绝后未创建玩家", status == 404, str(status))

    status, first = call(BASE, "/api/visit", payload, headers=headers(player, "r1"))
    ok &= check(
        "表单引号/换行/Unicode 正常入账",
        status == 200 and first["player"]["name"] == "阿飞" and first["visit"]["action"] == "点菜",
        str(status),
    )
    status, ledger_body = call(BASE, f"/api/ledger/{player}", headers=headers(player))
    stored = ledger_body["events"][0]["utterance"] if status == 200 else None
    ok &= check("原始正文逐字保真", stored == payload["utterance"], repr(stored))

    status, replay = call(BASE, "/api/visit", payload, headers=headers(player, "r1"))
    status2, ledger_after = call(BASE, f"/api/ledger/{player}", headers=headers(player))
    ok &= check(
        "重复 request_id 不重复记账",
        status == 200
        and replay == first
        and status2 == 200
        and len(ledger_after["events"]) == 1,
        f"events={len(ledger_after['events']) if status2 == 200 else '?'}",
    )

    status, second = call(
        BASE,
        "/api/visit",
        {
            "utterance": "掌柜的，还认得我吗？",
            "conversation_id": "c2",
            "action": "叙旧",
            "keywords": "断肠面",
        },
        headers=headers(player, "r2"),
    )
    ok &= check(
        "跨会话记忆",
        status == 200
        and second["visit"]["is_returning"] is True
        and second["player"]["visit_no"] == 2
        and any("来碗面" in memory for memory in second["memories"]),
        str(second.get("memories") if isinstance(second, dict) else second),
    )

    status, _ = ledger(BASE, player, owner=False)
    ok &= check("未授权 ledger 拒绝", status == 403, str(status))
    status, _ = ledger(BASE, player, owner=False, admin=True)
    ok &= check("管理员 ledger 可用", status == 200, str(status))
    status, _ = call(
        BASE,
        f"/api/players/{player}/reset",
        headers={"X-Inn-Service-Token": SERVICE},
        method="POST",
    )
    ok &= check("无管理员 token reset 拒绝", status == 403, str(status))

    if BASE_2:
        concurrent_player = "reg-" + uuid.uuid4().hex
        reset(BASE, concurrent_player)
        barrier = threading.Barrier(2)

        def send(base, request_id):
            barrier.wait(timeout=10)
            return call(
                base,
                "/api/visit",
                {"utterance": "同时点一碗面", "conversation_id": "cc", "action": "点菜"},
                headers=headers(concurrent_player, request_id),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send, base, f"cc-{index}") for index, base in enumerate((BASE, BASE_2))]
            results = [future.result() for future in futures]
        status, final = ledger(BASE, concurrent_player)
        ok &= check(
            "跨进程并发不丢更新",
            all(code == 200 for code, _ in results)
            and status == 200
            and final["player"]["reputation"] == 4
            and len(final["events"]) == 2,
            f"reputation={final.get('player', {}).get('reputation') if status == 200 else '?'}",
        )
        reset(BASE, concurrent_player)

        same_player = "reg-" + uuid.uuid4().hex
        reset(BASE, same_player)
        same_barrier = threading.Barrier(2)

        def send_same(base):
            same_barrier.wait(timeout=10)
            return call(
                base,
                "/api/visit",
                {"utterance": "同一个请求只记一次", "conversation_id": "cs", "action": "点菜"},
                headers=headers(same_player, "same-request"),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(send_same, base) for base in (BASE, BASE_2)]
            same_results = [future.result() for future in futures]
        status, same_final = ledger(BASE, same_player)
        ok &= check(
            "并发同 request_id 只记一次",
            all(code == 200 for code, _ in same_results)
            and status == 200
            and same_final["player"]["reputation"] == 2
            and len(same_final["events"]) == 1,
            f"reputation={same_final.get('player', {}).get('reputation') if status == 200 else '?'}",
        )
        reset(BASE, same_player)
    else:
        print("[SKIP] 跨进程并发：未设置 INN_BASE_URL_2")

    status, _ = reset(BASE, player)
    status2, _ = ledger(BASE, player)
    ok &= check("reset 后 ledger 404", status == 200 and status2 == 404, f"{status}/{status2}")

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
