"""WP6 orphan reaper unit tests (no Docker)."""
from __future__ import annotations

import pathlib
import sys
import time
import types
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.tasks.models import TaskStatus  # noqa: E402
from app.tasks.reaper import reap_orphans  # noqa: E402


class FakeService:
    enabled = True

    def __init__(self) -> None:
        self.records: dict[str, types.SimpleNamespace] = {}
        self.finished: list[tuple[str, TaskStatus]] = []

    def add(self, task_id, status, lease_until=None):
        self.records[task_id] = types.SimpleNamespace(
            status=status, lease_until=lease_until,
            terminal=status in (TaskStatus.COMPLETED, TaskStatus.FAILED,
                                TaskStatus.CANCELLED, TaskStatus.INTERRUPTED),
        )

    def get_record(self, task_id):
        return self.records.get(task_id)

    def finish(self, task_id, status, **kwargs):
        self.finished.append((task_id, status))
        return None


class FakeSandbox:
    def __init__(self, containers):
        self.containers = containers
        self.destroyed = []

    def list_containers(self):
        return list(self.containers)

    def destroy(self, task_id):
        self.destroyed.append(task_id)
        return {"ok": True, "verified": True, "container": f"yaig-{task_id}"}


class ReaperTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1_000_000.0
        self.service = FakeService()
        self.service.add("tk_done", TaskStatus.COMPLETED)
        self.service.add("tk_queued", TaskStatus.QUEUED)
        self.service.add("tk_active", TaskStatus.RUNNING, lease_until=self.now + 60)
        self.service.add("tk_expired", TaskStatus.RUNNING, lease_until=self.now - 10)

    def _container(self, task_id, age=120):
        return {"name": f"yaig-{task_id}", "task_id": task_id,
                "created_at": self.now - age, "status": "Exited (0)"}

    def test_terminal_queued_expired_unknown_removed_active_kept(self) -> None:
        sandbox = FakeSandbox([
            self._container("tk_done"),
            self._container("tk_queued"),
            self._container("tk_active"),
            self._container("tk_expired"),
            self._container("tk_unknown"),
        ])
        result = reap_orphans(self.service, sandbox, now=self.now)
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            sorted(item["task_id"] for item in result["removed"]),
            ["tk_done", "tk_queued", "tk_unknown"],
        )
        self.assertEqual(sandbox.destroyed, ["tk_done", "tk_queued", "tk_unknown"])
        kept = {item["task_id"]: item["reason"] for item in result["kept"]}
        self.assertEqual(kept["tk_active"], "active_task")
        self.assertEqual(kept["tk_expired"], "active_task")
        self.assertEqual(self.service.finished, [])

    def test_grace_window_keeps_recent_terminal_container(self) -> None:
        sandbox = FakeSandbox([self._container("tk_done", age=5)])
        result = reap_orphans(self.service, sandbox, now=self.now, grace_s=60)
        self.assertEqual(result["removed"], [])
        self.assertEqual(sandbox.destroyed, [])

    def test_dry_run_does_not_destroy(self) -> None:
        sandbox = FakeSandbox([self._container("tk_done")])
        result = reap_orphans(self.service, sandbox, now=self.now, dry_run=True)
        self.assertEqual(len(result["would_remove"]), 1)
        self.assertEqual(sandbox.destroyed, [])

    def test_disabled_store_skips(self) -> None:
        service = FakeService()
        service.enabled = False
        result = reap_orphans(service, FakeSandbox([self._container("x")]), now=self.now)
        self.assertEqual(result["skipped_reason"], "task store disabled")

    def test_database_read_error_aborts_without_destroy(self) -> None:
        class FaultyService(FakeService):
            def get_record(self, task_id):
                raise OSError("database is unavailable")

        sandbox = FakeSandbox([self._container("tk_active")])
        result = reap_orphans(FaultyService(), sandbox, now=self.now)
        self.assertFalse(result["ok"])
        self.assertEqual(result["removed"], [])
        self.assertEqual(sandbox.destroyed, [])
        self.assertTrue(any("read failed" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
