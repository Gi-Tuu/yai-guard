"""Task store contract for WP1.

This test suite does not touch FastAPI or the running app; it only proves the
durable state machine that later phases will wire in.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.tasks import ApprovalStatus, TaskAlreadyExists, TaskStatus, TaskStore  # noqa: E402


class TaskStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "tasks.db"
        self.store = TaskStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def _create(self, task_id: str, *, owner: str = "u1") -> None:
        self.store.create_task(
            task_id=task_id, trace_id="tr_" + task_id, owner=owner, prompt="ls",
        )

    def _running(self, task_id: str = "tk1") -> None:
        self._create(task_id)
        record = self.store.claim_next(worker_id="w1")
        self.assertIsNotNone(record)
        self.assertEqual(record.id, task_id)

    # ---------------------------------------------------------------- create
    def test_create_and_reopen(self) -> None:
        self._create("tk1")
        record = self.store.get_task("tk1")
        self.assertIsNotNone(record)
        self.assertEqual(record.status, TaskStatus.QUEUED)
        self.assertFalse(record.cancel_requested)

        self.store.close()
        self.store = TaskStore(self.path)
        self.assertEqual(self.store.get_task("tk1").status, TaskStatus.QUEUED)

    def test_duplicate_id_or_trace_is_rejected(self) -> None:
        self._create("tk1")
        with self.assertRaises(TaskAlreadyExists):
            self.store.create_task(task_id="tk1", trace_id="tr_other", owner="u1", prompt="x")
        with self.assertRaises(TaskAlreadyExists):
            self.store.create_task(task_id="tk_other", trace_id="tr_tk1", owner="u1", prompt="x")

    def test_owner_listing(self) -> None:
        self._create("tk1", owner="u1")
        self._create("tk2", owner="u2")
        self.assertEqual({r.id for r in self.store.list_tasks(owner="u1")}, {"tk1"})
        self.assertEqual(len(self.store.list_tasks(status=TaskStatus.QUEUED)), 2)

    # ----------------------------------------------------------- transitions
    def test_allowed_and_invalid_transitions(self) -> None:
        self._create("tk1")
        self.assertIsNotNone(self.store.transition("tk1", TaskStatus.RUNNING))
        self.assertIsNotNone(self.store.transition("tk1", TaskStatus.WAITING_APPROVAL))
        self.assertIsNotNone(self.store.transition("tk1", TaskStatus.RUNNING))
        record = self.store.finish("tk1", TaskStatus.COMPLETED, summary="ok")
        self.assertIsNotNone(record)
        self.assertEqual(record.status, TaskStatus.COMPLETED)
        self.assertIsNotNone(record.finished_at)
        self.assertIsNone(self.store.transition("tk1", TaskStatus.RUNNING))

    def test_terminal_is_immutable(self) -> None:
        self._running()
        self.assertIsNotNone(self.store.finish("tk1", TaskStatus.FAILED, error="boom"))
        self.assertIsNone(self.store.finish("tk1", TaskStatus.COMPLETED))
        self.assertFalse(self.store.request_cancel("tk1"))

    def test_version_cas(self) -> None:
        self._create("tk1")
        record = self.store.get_task("tk1")
        self.assertIsNotNone(
            self.store.transition("tk1", TaskStatus.RUNNING, expected_version=record.version)
        )
        old = self.store.get_task("tk1")
        self.assertIsNone(
            self.store.transition(
                "tk1", TaskStatus.WAITING_APPROVAL, expected_version=record.version
            )
        )
        self.assertNotEqual(old.version, record.version)

    # ----------------------------------------------------------------- claim
    def test_claim_is_single_owner(self) -> None:
        self._create("tk1")
        first = self.store.claim_next(worker_id="w1")
        second = self.store.claim_next(worker_id="w2")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(self.store.get_task("tk1").worker_id, "w1")

    def test_claim_orders_by_created_at(self) -> None:
        self.store.create_task(
            task_id="later", trace_id="tr_later", owner="u1", prompt="x", now=20.0
        )
        self.store.create_task(
            task_id="earlier", trace_id="tr_earlier", owner="u1", prompt="x", now=10.0
        )
        self.assertEqual(self.store.claim_next(worker_id="w1").id, "earlier")

    def test_heartbeat_only_for_owner(self) -> None:
        self._running()
        self.assertTrue(self.store.heartbeat("tk1", worker_id="w1"))
        self.assertFalse(self.store.heartbeat("tk1", worker_id="w2"))

    # -------------------------------------------------------------- recovery
    def test_recover_interrupts_only_active_tasks(self) -> None:
        self._running("running")
        self._create("waiting")
        claimed = self.store.claim_next(worker_id="w1")
        self.assertEqual(claimed.id, "waiting")
        self.store.transition("waiting", TaskStatus.WAITING_APPROVAL)
        self._create("completed")
        self.store.transition("completed", TaskStatus.RUNNING)
        self.store.finish("completed", TaskStatus.COMPLETED, summary="ok")
        self._create("queued")

        interrupted = self.store.recover_interrupted(reason="restart")
        self.assertEqual(set(interrupted), {"running", "waiting"})
        self.assertEqual(self.store.get_task("running").status, TaskStatus.INTERRUPTED)
        self.assertEqual(self.store.get_task("waiting").status, TaskStatus.INTERRUPTED)
        self.assertEqual(self.store.get_task("completed").status, TaskStatus.COMPLETED)
        self.assertEqual(self.store.get_task("queued").status, TaskStatus.QUEUED)

    # -------------------------------------------------------------- cancel
    def test_cancel_is_flag_not_terminal(self) -> None:
        self._running()
        self.assertTrue(self.store.request_cancel("tk1"))
        record = self.store.get_task("tk1")
        self.assertTrue(record.cancel_requested)
        self.assertEqual(record.status, TaskStatus.RUNNING)
        self.assertFalse(self.store.request_cancel("tk1"))

    # ------------------------------------------------------------- approvals
    def test_approvals_are_idempotent(self) -> None:
        self._create("tk1")
        self.store.create_approval(
            task_id="tk1", call_id="c1", owner="u1", ttl_s=30,
        )
        pending = self.store.pending_approvals("tk1")
        self.assertEqual(len(pending), 1)
        self.assertTrue(self.store.resolve_approval(
            "tk1", "c1", status=ApprovalStatus.APPROVED,
        ))
        self.assertFalse(self.store.resolve_approval(
            "tk1", "c1", status=ApprovalStatus.REJECTED,
        ))
        self.assertEqual(self.store.get_approval("tk1", "c1")["status"], "approved")

    def test_counts(self) -> None:
        self._create("q1")
        self._create("q2")
        self.store.claim_next(worker_id="w1")
        counts = self.store.count_by_status()
        self.assertEqual(counts[TaskStatus.QUEUED.value], 1)
        self.assertEqual(counts[TaskStatus.RUNNING.value], 1)

    def test_list_queued_excludes_claimed_tasks(self) -> None:
        self._create("q1")
        self._create("q2")
        self.store.claim_next(worker_id="w1")
        self.assertEqual([record.id for record in self.store.list_queued()], ["q2"])

    def test_prune_removes_only_old_terminal_tasks(self) -> None:
        self._create("old")
        self.store.transition("old", TaskStatus.FAILED, finished_at=1000,
                              error="old failure")
        self.store.create_approval(task_id="old", call_id="c1", owner="u1", ttl_s=60)
        self._create("active")
        listed = self.store.list_terminal_before(2000)
        self.assertEqual([record.id for record in listed], ["old"])
        removed = self.store.prune_terminal_before(2000)
        self.assertEqual([record.id for record in removed], ["old"])
        self.assertIsNone(self.store.get_task("old"))
        self.assertIsNotNone(self.store.get_task("active"))
        self.assertIsNone(self.store.get_approval("old", "c1"))

    def test_schema_version_is_recorded_and_reopen_is_noop(self) -> None:
        version = self.store._conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(int(version), 2)
        self.store.close()
        reopened = TaskStore(self.path)
        try:
            self.assertEqual(
                int(reopened._conn.execute("PRAGMA user_version").fetchone()[0]), 2,
            )
        finally:
            reopened.close()

    def test_v1_database_migrates_to_v2(self) -> None:
        import sqlite3
        from app.tasks.store import _migration_v1

        v1_path = pathlib.Path(self.tmp.name) / "v1.db"
        conn = sqlite3.connect(v1_path)
        _migration_v1(conn)
        conn.execute("PRAGMA user_version=1")
        conn.commit()
        conn.close()
        migrated = TaskStore(v1_path)
        try:
            self.assertEqual(
                int(migrated._conn.execute("PRAGMA user_version").fetchone()[0]), 2,
            )
            columns = {row[1] for row in migrated._conn.execute("PRAGMA table_info(tasks)")}
            self.assertIn("updated_at", columns)
            migrated.create_task(task_id="new", trace_id="tr_new", owner="u", prompt="x")
            self.assertIsNotNone(migrated.get_task("new"))
        finally:
            migrated.close()


if __name__ == "__main__":
    unittest.main()
