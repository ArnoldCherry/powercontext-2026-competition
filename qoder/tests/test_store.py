from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from board.store import Store


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp_dir.name) / "board.db")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_task(self, task_id: str, **overrides):
        values = {
            "id": task_id,
            "title": f"任务 {task_id}",
            "direction": "PowerContext",
            "difficulty": "A",
            "status": "todo",
            "owner": "DS-02",
            "priority": "P1",
            "next_action": "下一步",
            "evidence": [{"label": "日志", "url": "https://example.com/log"}],
        }
        values.update(overrides)
        return self.store.create_task(values)

    def test_create_and_get_task(self) -> None:
        task = self.make_task("DS-02")
        loaded = self.store.get_task("DS-02")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["title"], "任务 DS-02")
        self.assertEqual(loaded["evidence"], [{"label": "日志", "url": "https://example.com/log"}])
        self.assertEqual(task["id"], "DS-02")

    def test_filters_and_search(self) -> None:
        self.make_task("DS-02", status="ready")
        self.make_task("DS-03", status="blocked", owner="DS-03")
        self.make_task("DS-04", direction="Qoder", status="ready")

        self.assertEqual(len(self.store.list_tasks()), 3)
        self.assertEqual(len(self.store.list_tasks(status="ready")), 2)
        self.assertEqual(len(self.store.list_tasks(direction="Qoder")), 1)
        self.assertEqual(len(self.store.list_tasks(query="DS-03")), 1)
        self.assertEqual(next(task["id"] for task in self.store.list_tasks()), "DS-03")

    def test_update_and_stats(self) -> None:
        self.make_task("DS-02")
        updated = self.store.update_task(
            "DS-02",
            {
                "status": "scored",
                "score": 18,
                "submission_url": "https://example.com/submission",
                "evidence": [{"label": "提交页", "url": "https://example.com/submission"}],
            },
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["status"], "scored")
        self.assertEqual(updated["score"], 18.0)
        self.assertEqual(updated["evidence"][0]["label"], "提交页")

        stats = self.store.stats()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["done"], 1)
        self.assertEqual(stats["completion_rate"], 100.0)
        self.assertEqual(stats["score_sum"], 18.0)

    def test_meta(self) -> None:
        self.store.set_meta("powercontext_scope_id", "scp_demo")
        self.assertEqual(self.store.get_meta("powercontext_scope_id"), "scp_demo")
        self.store.set_meta("powercontext_scope_id", "scp_next")
        self.assertEqual(self.store.get_meta("powercontext_scope_id"), "scp_next")


if __name__ == "__main__":
    unittest.main()
