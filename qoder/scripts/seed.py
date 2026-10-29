from __future__ import annotations

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from board.config import load_settings
from board.memory import MemoryService
from board.runtime import PowerContextRuntime, PowerContextRuntimeError
from board.seed import seed_board
from board.store import Store


def main() -> int:
    parser = argparse.ArgumentParser(description="重置/初始化生态赛看板与自管理 PowerContext 记忆")
    parser.add_argument("--reset", action="store_true", help="清空任务后重新写入种子数据")
    args = parser.parse_args()

    settings = load_settings()
    store = Store(settings.db_path)
    runtime = PowerContextRuntime(settings)
    try:
        runtime.start()
    except PowerContextRuntimeError as error:
        print(f"PowerContext 启动失败：{error}")
        return 2
    try:
        memory = MemoryService(settings, store)
        result = seed_board(
            store,
            memory,
            reset=args.reset,
            receipt_path=settings.db_path.parent / "seed_receipt.json",
        )
    finally:
        runtime.stop()

    if result.get("memory_error"):
        print(f"任务已写入，但记忆初始化失败：{result['memory_error']}")
        return 2
    print(f"Scope: {result['scope_id']}")
    print(f"任务总数: {result['task_count']}（本次新增 {result['created_now']}）")
    print(f"进展记忆: {len(result.get('seeded_progress') or [])} 条")
    print(f"凭据: {settings.db_path.parent / 'seed_receipt.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
