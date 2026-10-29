from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from board.config import ROOT, load_settings
from board.memory import LOCAL_TIMEZONE, MemoryService
from board.store import Store

SEED_VERSION = "1"

TASKS = [
    {
        "id": "DS-00",
        "title": "修复已有 HTTP / CSV / 增量 / Pi / Dify 能力",
        "direction": "PowerContext",
        "difficulty": "S",
        "status": "doing",
        "owner": "DS-00",
        "priority": "P0",
        "repo": "ArnoldCherry/powercontext-2026-competition",
        "branch": "2026-competition",
        "next_action": "完成回归测试，把已验证能力写入交付报告",
        "notes": "示例数据：用于演示筛选、证据和记忆接力。",
    },
    {
        "id": "DS-01",
        "title": "平台提交与证据跟踪",
        "direction": "提交与验收",
        "difficulty": "S",
        "status": "doing",
        "owner": "DS-01",
        "priority": "P0",
        "next_action": "补齐待提交任务的材料清单与平台任务编号",
        "notes": "示例数据：只有 DS-01 操作大赛平台。",
    },
    {
        "id": "DS-02",
        "title": "Embedding 合并与缓存",
        "direction": "PowerContext",
        "difficulty": "A",
        "status": "ready",
        "owner": "DS-02",
        "priority": "P1",
        "repo": "ArnoldCherry/powercontext-2026-competition",
        "branch": "feat/memory-02-embedding-batches",
        "commit_hash": "dab24006",
        "next_action": "提交到测评平台并回填任务编号与得分",
    },
    {
        "id": "DS-03",
        "title": "Source 生命周期 DAG",
        "direction": "PowerContext",
        "difficulty": "A",
        "status": "doing",
        "owner": "DS-03",
        "priority": "P1",
        "repo": "ArnoldCherry/powercontext-2026-competition",
        "branch": "feat/memory-01-source-lifecycle",
        "commit_hash": "dab24006",
        "next_action": "补一组增量摄取回归样例",
    },
    {
        "id": "DS-04",
        "title": "查询驱动原文证据选择",
        "direction": "PowerContext",
        "difficulty": "A",
        "status": "blocked",
        "owner": "DS-04",
        "priority": "P0",
        "repo": "ArnoldCherry/powercontext-2026-competition",
        "branch": "feat/context-01-evidence-selection",
        "commit_hash": "dab24006",
        "next_action": "等待回归环境与端口释放后继续证据页测试",
    },
    {
        "id": "DS-05",
        "title": "财报 PDF 全文与证据页检索",
        "direction": "PowerContext",
        "difficulty": "B",
        "status": "todo",
        "owner": "DS-05",
        "priority": "P1",
        "repo": "ArnoldCherry/powercontext-2026-competition",
        "branch": "feat/source-03-pdf-retrieval",
        "next_action": "先准备一组可复现的财报 PDF 样例",
    },
    {
        "id": "DS-06",
        "title": "Qoder 提效项目与 Sites 发布",
        "direction": "Qoder",
        "difficulty": "A",
        "status": "doing",
        "owner": "DS-06",
        "priority": "P0",
        "repo": "ArnoldCherry/powercontext-2026-competition",
        "branch": "feat/eco-qoder",
        "next_action": "完成看板、记忆验证并发布 Qoder Sites",
        "notes": "本项目：任务与提交证据看板。",
    },
    {
        "id": "DS-07",
        "title": "MiniMax SDD 上下文系统",
        "direction": "MiniMax",
        "difficulty": "B",
        "status": "todo",
        "owner": "DS-07",
        "priority": "P1",
        "next_action": "确认 MiniMax 接口额度与最小演示链路",
    },
    {
        "id": "DS-08",
        "title": "单 SQL 混合检索",
        "direction": "PowerContext",
        "difficulty": "A",
        "status": "todo",
        "owner": "DS-08",
        "priority": "P1",
        "branch": "feat/retrieval-02-hybrid-search",
        "next_action": "整理单 SQL 的基准查询与对比数据",
    },
    {
        "id": "DS-09",
        "title": "端侧推理与 seekdb 混合检索",
        "direction": "StepFun",
        "difficulty": "B",
        "status": "todo",
        "owner": "DS-09",
        "priority": "P2",
        "branch": "feat/eco-stepfun",
        "next_action": "确认端侧模型与 seekdb 的最小可运行组合",
    },
    {
        "id": "DEMO-01",
        "title": "验证样例：状态更新写入记忆",
        "direction": "验证样例",
        "difficulty": "B",
        "status": "doing",
        "owner": "QA",
        "priority": "P1",
        "next_action": "运行 scripts/verify.py 完成偏好与进展读写验证",
        "notes": "评审可安全修改此任务，不会影响真实交付数据。",
    },
]

PROGRESS_SEEDS = [
    ("DEMO-01", "验证样例任务已就绪，等待运行 verify.py", 1),
    ("DS-06", "看板后端与界面完成，接入 PowerContext 记忆", 0.5),
    ("DS-04", "证据选择测试被环境占用阻塞，等待端口释放", 2),
    ("DS-02", "Embedding 合并与缓存已通过自测，等待 DS-01 提交", 3),
    ("DS-01", "已整理三份可提交材料，正在补齐任务编号", 4),
    ("DS-00", "完成 Dify 场景修复，继续 HTTP / CSV 回归", 5),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="初始化生态赛交付看板与 PowerContext 记忆")
    parser.add_argument("--reset", action="store_true", help="清空任务后重新写入种子数据")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()
    store = Store(settings.db_path)
    memory = MemoryService(settings, store)

    health = memory.health()
    if health["status"] != "online":
        print(f"PowerContext 不可用：{health.get('error', health['status'])}")
        print("请先启动 PowerContext，再运行 seed.py。")
        return 2

    if args.reset:
        store.clear_tasks()
        store.set_meta("powercontext_scope_id", "")
        store.set_meta("seed_version", "")

    scope_id = memory.ensure_scope()
    created = 0
    for task in TASKS:
        if store.get_task(task["id"]):
            continue
        store.create_task(task)
        created += 1

    seeded = store.get_meta("seed_version") == SEED_VERSION
    preference = {
        "board_title": "生态赛交付看板",
        "default_direction": "全部",
        "default_status": "全部",
        "default_difficulty": "全部",
    }
    preference_result = memory.save_preference(preference)

    progress_results = []
    if not seeded or args.reset:
        for task_id, next_action, hours_ago in PROGRESS_SEEDS:
            task = store.get_task(task_id)
            if not task:
                continue
            task = {**task, "next_action": next_action}
            at = (datetime.now(LOCAL_TIMEZONE) - timedelta(hours=hours_ago)).replace(microsecond=0).isoformat()
            payload = {
                "task": task["id"],
                "status": task["status"],
                "title": task["title"],
                "next": next_action,
                "at": at,
            }
            text = f"[progress] {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
            memory.remember("progress", text, "初始化看板进展")
            progress_results.append(payload)
        store.set_meta("seed_version", SEED_VERSION)
        store.set_meta("seeded_at", datetime.now(UTC).replace(microsecond=0).isoformat())

    receipt = {
        "generated_at": datetime.now(LOCAL_TIMEZONE).replace(microsecond=0).isoformat(),
        "scope_id": scope_id,
        "db_path": str(settings.db_path),
        "task_count": store.count_tasks(),
        "created_now": created,
        "preference": preference_result,
        "seeded_progress": progress_results,
    }
    receipt_path = Path(ROOT) / "data" / "seed_receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Scope: {scope_id}")
    print(f"任务总数: {receipt['task_count']}（本次新增 {created}）")
    print(f"偏好已写入 PowerContext: {preference_result['preference']['board_title']}")
    print(f"进展记忆: {len(progress_results)} 条")
    print(f"凭据: {receipt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
