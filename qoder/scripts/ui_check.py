from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import json
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = PROJECT_ROOT / "evidence"
BASE_URL = "http://127.0.0.1:18800"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

results: list[dict[str, object]] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    results.append({"name": name, "ok": bool(condition), "detail": detail})
    print(f"[{'PASS' if condition else 'FAIL'}] {name}")


def restore_state() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=20) as client:
        client.put(
            "/api/preferences",
            json={
                "board_title": "生态赛交付看板",
                "default_direction": "全部",
                "default_status": "全部",
                "default_difficulty": "全部",
            },
        )
        client.patch("/api/tasks/DEMO-01", json={"status": "ready"})


def main() -> int:
    EVIDENCE.mkdir(exist_ok=True)
    executable = EDGE if Path(EDGE).exists() else CHROME
    console_errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=executable)
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
            page.on(
                "console",
                lambda message: console_errors.append(message.text) if message.type == "error" else None,
            )
            page.on("pageerror", lambda error: console_errors.append(str(error)))

            page.goto(BASE_URL, wait_until="networkidle")
            page.wait_for_timeout(500)
            check("页面标题来自记忆偏好", page.title() == "生态赛交付看板 · Qoder × OceanBase", page.title())
            check("任务卡片数量", page.locator(".task-card").count() == 11, page.locator(".task-card").count())
            check("记忆状态在线", "记忆在线" in page.locator("#memory-badge").inner_text())
            check("进展时间线可见", page.locator(".timeline-item").count() >= 6, page.locator(".timeline-item").count())
            page.screenshot(path=str(EVIDENCE / "ui-overview.png"), full_page=True)

            demo = page.locator(".task-card", has_text="DEMO-01").first
            select = demo.locator("select")
            with page.expect_response(lambda response: "/api/tasks/DEMO-01" in response.url and response.request.method == "PATCH"):
                select.select_option("doing")
            page.wait_for_timeout(900)
            check("状态选择后任务更新", page.locator(".task-card", has_text="DEMO-01").first.locator("select").input_value() == "doing")
            demo = page.locator(".task-card", has_text="DEMO-01").first
            with page.expect_response(lambda response: "/api/tasks/DEMO-01" in response.url and response.request.method == "PATCH"):
                demo.locator("select").select_option("ready")
            page.wait_for_timeout(900)
            check("状态可恢复为待提交", page.locator(".task-card", has_text="DEMO-01").first.locator("select").input_value() == "ready")

            page.locator("#filter-status").select_option("blocked")
            page.wait_for_timeout(300)
            check("按状态筛选阻塞任务", page.locator(".task-card").count() == 1, page.locator("#result-count").inner_text())
            check("阻塞任务为 DS-04", "DS-04" in page.locator(".task-card").first.inner_text())
            page.screenshot(path=str(EVIDENCE / "ui-filter-blocked.png"), full_page=True)
            page.locator("#clear-filters").click()

            page.locator("#search-input").fill("PDF")
            page.wait_for_timeout(400)
            check("关键词搜索 PDF", page.locator(".task-card").count() == 1 and "DS-05" in page.locator(".task-card").first.inner_text())
            page.locator("#clear-filters").click()
            check("清除筛选恢复全部任务", page.locator(".task-card").count() == 11)

            page.locator(".task-card", has_text="DS-06").first.locator(".icon-button").click()
            expect(page.locator("#task-dialog")).to_be_visible()
            page.wait_for_timeout(300)
            check("编辑弹窗展示任务数据", page.locator("#field-branch").input_value() == "feat/eco-qoder")
            page.screenshot(path=str(EVIDENCE / "ui-edit-dialog.png"))
            page.locator("#dialog-cancel").click()

            page.locator("#pref-title").fill("生态赛交付看板 · 偏好验证")
            page.locator("#pref-direction").select_option("Qoder")
            page.locator("#pref-status").select_option("全部")
            page.screenshot(path=str(EVIDENCE / "ui-preference-panel.png"))
            page.locator("#pref-form button[type=submit]").click()
            page.wait_for_timeout(1200)
            check("保存偏好后标题更新", page.locator("#board-title").inner_text() == "生态赛交付看板 · 偏好验证")

            page.reload(wait_until="networkidle")
            page.wait_for_timeout(500)
            check("刷新后标题由记忆恢复", page.locator("#board-title").inner_text() == "生态赛交付看板 · 偏好验证")
            check("刷新后方向偏好生效", page.locator("#filter-direction").input_value() == "Qoder")
            check("刷新后仅显示 Qoder 任务", page.locator(".task-card").count() == 1 and "DS-06" in page.locator(".task-card").first.inner_text())

            restore_state()
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(500)
            check("恢复默认偏好后显示全部任务", page.locator(".task-card").count() == 11 and page.locator("#board-title").inner_text() == "生态赛交付看板")

            page.set_viewport_size({"width": 390, "height": 844})
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(500)
            check("移动端无横向溢出", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
            page.screenshot(path=str(EVIDENCE / "ui-mobile.png"), full_page=True)

            check("浏览器控制台无错误", not console_errors, console_errors)
            browser.close()
    finally:
        restore_state()

    output = EVIDENCE / "ui_check.json"
    output.write_text(
        json.dumps({"checked_at": page_time(), "results": results, "console_errors": console_errors}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"证据: {output}")
    return 0 if all(item["ok"] for item in results) else 1


def page_time() -> str:
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone(timedelta(hours=8))).replace(microsecond=0).isoformat()


if __name__ == "__main__":
    raise SystemExit(main())
