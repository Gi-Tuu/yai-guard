"""WP2 TaskService bridge: opt-in SQLite mirror, memory fallback, degradation."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.tasks import TaskService, TaskStatus, TaskStore  # noqa: E402


class TaskServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "tasks.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_memory_mode_is_noop_and_healthy(self) -> None:
        service = TaskService()
        self.assertFalse(service.enabled)
        self.assertIsNone(service.create(
            task_id="tk", trace_id="tr", owner="u", prompt="ls",
        ))
        self.assertEqual(service.health(), {
            "ok": True, "mode": "memory", "degraded": False, "error": None,
        })

    def test_sqlite_creates_and_persists(self) -> None:
        service = TaskService.from_path(self.path)
        self.assertTrue(service.enabled)
        record = service.create(
            task_id="tk", trace_id="tr", owner="u", prompt="ls",
        )
        self.assertIsNotNone(record)
        self.assertEqual(record.status, TaskStatus.QUEUED)
        self.assertIsNotNone(service.mark_running("tk"))
        self.assertEqual(service.get_record("tk").status, TaskStatus.RUNNING)
        self.assertIsNotNone(service.finish("tk", TaskStatus.COMPLETED, summary="ok"))
        self.assertEqual(service.get_record("tk").status, TaskStatus.COMPLETED)
        service.close()

    def test_restart_recovers_active_tasks(self) -> None:
        first = TaskService.from_path(self.path)
        first.create(task_id="running", trace_id="tr1", owner="u", prompt="x")
        first.mark_running("running")
        first.create(task_id="queued", trace_id="tr2", owner="u", prompt="x")
        first.close()

        second = TaskService.from_path(self.path)
        self.assertEqual(second.get_record("running").status, TaskStatus.INTERRUPTED)
        self.assertEqual(second.get_record("queued").status, TaskStatus.QUEUED)
        second.close()

    def test_failed_store_degrades_instead_of_breaking(self) -> None:
        service = TaskService.from_path(self.tmp.name)  # directory, not a DB
        self.assertFalse(service.enabled)
        self.assertTrue(service.degraded)
        self.assertFalse(service.health()["ok"])
        self.assertIsNone(service.create(
            task_id="tk", trace_id="tr", owner="u", prompt="ls",
        ))

    def test_close_memory_is_safe(self) -> None:
        TaskService().close()


if __name__ == "__main__":
    unittest.main()
