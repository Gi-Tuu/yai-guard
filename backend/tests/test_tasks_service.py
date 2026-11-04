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

    def test_waiting_approval_transitions(self) -> None:
        service = TaskService.from_path(self.path)
        service.create(task_id="tk", trace_id="tr", owner="u", prompt="ls")
        service.mark_running("tk")
        self.assertIsNotNone(service.mark_waiting("tk"))
        self.assertEqual(service.get_record("tk").status, TaskStatus.WAITING_APPROVAL)
        self.assertIsNotNone(service.mark_resumed("tk"))
        self.assertEqual(service.get_record("tk").status, TaskStatus.RUNNING)
        service.finish("tk", TaskStatus.COMPLETED)
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

    def test_cancel_request_is_persisted(self) -> None:
        service = TaskService.from_path(self.path)
        service.create(task_id="tk", trace_id="tr", owner="u", prompt="ls")
        service.mark_running("tk")
        self.assertTrue(service.request_cancel("tk"))
        self.assertTrue(service.get_record("tk").cancel_requested)
        service.close()

    def test_approval_lifecycle_is_persisted(self) -> None:
        service = TaskService.from_path(self.path)
        service.create(task_id="tk", trace_id="tr", owner="u", prompt="ls")
        self.assertTrue(service.create_approval(
            task_id="tk", call_id="call_1", owner="u", ttl_s=60,
        ))
        pending = service.pending_approvals("tk")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["call_id"], "call_1")
        self.assertTrue(service.resolve_approval("tk", "call_1", "approved"))
        self.assertEqual(service.get_approval("tk", "call_1")["status"], "approved")
        self.assertFalse(service.resolve_approval("tk", "call_1", "rejected"))
        self.assertEqual(service.pending_approvals("tk"), [])
        service.close()

    def test_memory_approval_is_noop(self) -> None:
        service = TaskService()
        self.assertFalse(service.create_approval(
            task_id="tk", call_id="call_1", owner="u", ttl_s=60,
        ))
        self.assertFalse(service.resolve_approval("tk", "call_1", "approved"))
        self.assertIsNone(service.get_approval("tk", "call_1"))


if __name__ == "__main__":
    unittest.main()
