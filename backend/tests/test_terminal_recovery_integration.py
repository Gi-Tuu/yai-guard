"""End-to-end terminal recovery using real TaskService, SQLite and TraceStore."""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

os.environ.setdefault("ARTIFACT_DIR", tempfile.mkdtemp(prefix="yaig-recovery-"))
os.environ.setdefault("MOCK_MODEL", "1")
os.environ.setdefault("SANDBOX_DRIVER", "fake")
os.environ.setdefault("DEMO_TOKENS", "token-a")
os.environ.setdefault("RATE_LIMIT_PER_MIN", "0")

from app import maintenance, settings, task_state  # noqa: E402
from app.tasks import TaskService, TaskStatus, TaskStore  # noqa: E402
from app.trace import TraceStore  # noqa: E402

try:
    from fastapi.testclient import TestClient
    from app import main as api
    from app import security
except ImportError:  # pragma: no cover
    TestClient = None
    api = None
    security = None

TOKEN = {"X-Demo-Token": "token-a"}


class TerminalRecoveryIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="yaig-recovery-case-")
        self.root = pathlib.Path(self.tmp.name)
        self.service = TaskService.from_path(self.root / "tasks.db")
        self.old_tasks = dict(task_state.TASKS)
        task_state.TASKS.clear()

    def tearDown(self) -> None:
        task_state.TASKS.clear()
        task_state.TASKS.update(self.old_tasks)
        self.service.close()
        self.tmp.cleanup()

    def _make_running(self, task_id: str = "tk") -> str:
        self.service.create(task_id=task_id, trace_id=f"tr_{task_id}",
                            owner="u", prompt="x")
        self.service.mark_running(task_id)
        return task_id

    def _write_trace(self, task_id: str, *, final_event: str,
                     final_payload: dict, anchor: bool = True,
                     hmac_key: str | None = None) -> pathlib.Path:
        path = self.root / f"tr_{task_id}.jsonl"
        trace = TraceStore(path, f"tr_{task_id}")
        trace.append(task_id=task_id, actor="system", event="task.created",
                     payload={})
        trace.append(task_id=task_id, actor="agent", event=final_event,
                     payload=final_payload)
        if anchor:
            trace.seal(self.root / f"tr_{task_id}.anchor.json",
                       hmac_key=hmac_key)
        return path

    def _run(self) -> list[str]:
        with mock.patch.object(settings, "TRACES", self.root), \
                mock.patch.object(settings, "TASK_SERVICE", self.service), \
                mock.patch.object(settings, "TRACE_ED25519_KEY", None), \
                mock.patch.object(settings, "TRACE_ED25519_PUBLIC", None):
            return maintenance._record_interrupted_events()

    def test_completed_recovery_updates_db_and_api_cache(self) -> None:
        task_id = self._make_running()
        self._write_trace(
            task_id, final_event="task.completed",
            final_payload={"summary": "done"},
        )
        # Add a confirmed cleanup after task.completed; it is the strict proof.
        trace = TraceStore(self.root / f"tr_{task_id}.jsonl", f"tr_{task_id}")
        trace.append(task_id=task_id, actor="system",
                     event="sandbox.destroy.confirmed",
                     payload={"cleanup": {"verified": True}})
        trace.seal(self.root / f"tr_{task_id}.anchor.json")
        cached = task_state.TaskState(
            task_id=task_id, trace_id=f"tr_{task_id}", task="x",
            owner=security._principal_id("token-a") if security else "u",
            status="failed", error="terminal state was not persisted",
        )
        task_state.TASKS[task_id] = cached
        self.assertEqual(self._run(), [task_id])
        self.assertEqual(self.service.get_record(task_id).status,
                         TaskStatus.COMPLETED)
        self.assertEqual(cached.status, "completed")
        self.assertTrue(cached.cancel_event is not None)
        if TestClient is not None:
            with mock.patch.object(settings, "TASK_RUNTIME_MODE", "single"), \
                    mock.patch.object(settings, "TASK_SERVICE", self.service):
                res = TestClient(api.app).get(f"/api/tasks/{task_id}",
                                              headers=TOKEN)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["status"], "completed")

    def test_late_finalize_failure_does_not_overwrite_reconciled_cache(self) -> None:
        from app import task_runner

        task_id = self._make_running()
        trace = TraceStore(self.root / f"tr_{task_id}.jsonl", f"tr_{task_id}")
        trace.append(task_id=task_id, actor="system", event="task.created",
                     payload={})
        trace.append(task_id=task_id, actor="agent", event="task.completed",
                     payload={"summary": "done"})
        trace.append(task_id=task_id, actor="system",
                     event="sandbox.destroy.confirmed",
                     payload={"cleanup": {"verified": True}})
        trace.seal(self.root / f"tr_{task_id}.anchor.json")
        cached = task_state.TaskState(
            task_id=task_id, trace_id=f"tr_{task_id}", task="x",
            owner=security._principal_id("token-a") if security else "u",
            status="failed", worker_id="app", version=1,
        )
        task_state.TASKS[task_id] = cached
        ctx = task_runner.RunContext(
            state=cached, final_status="completed", final_summary="done",
        )

        def reconcile_then_fail(*args, **kwargs):
            # The periodic pass wins between trace seal and DB finalize.
            with mock.patch.object(settings, "TRACES", self.root), \
                    mock.patch.object(settings, "TASK_SERVICE", self.service), \
                    mock.patch.object(settings, "TRACE_ED25519_KEY", None), \
                    mock.patch.object(settings, "TRACE_ED25519_PUBLIC", None):
                maintenance._record_interrupted_events()
            return None

        with mock.patch.object(settings, "TASK_SERVICE", self.service), \
                mock.patch.object(settings, "TASK_RUNTIME_MODE", "single"), \
                mock.patch.object(self.service, "finish",
                                  side_effect=reconcile_then_fail):
            task_runner._finalize_run(ctx)
        self.assertEqual(self.service.get_record(task_id).status,
                         TaskStatus.COMPLETED)
        self.assertEqual(cached.status, "completed")
        if TestClient is not None:
            with mock.patch.object(settings, "TASK_RUNTIME_MODE", "single"), \
                    mock.patch.object(settings, "TASK_SERVICE", self.service):
                res = TestClient(api.app).get(f"/api/tasks/{task_id}",
                                              headers=TOKEN)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["status"], "completed")

    def test_bad_hmac_is_not_reconciled(self) -> None:
        task_id = self._make_running()
        self._write_trace(
            task_id, final_event="task.completed",
            final_payload={"summary": "done"}, hmac_key="good",
        )
        trace = TraceStore(self.root / f"tr_{task_id}.jsonl", f"tr_{task_id}")
        trace.append(task_id=task_id, actor="system",
                     event="sandbox.destroy.confirmed",
                     payload={"cleanup": {"verified": True}})
        trace.seal(self.root / f"tr_{task_id}.anchor.json", hmac_key="good")
        with mock.patch.dict(os.environ, {"TRACE_HMAC_KEY": "bad"}):
            recorded = self._run()
        self.assertEqual(recorded, [])
        self.assertEqual(self.service.get_record(task_id).status,
                         TaskStatus.RUNNING)

    def test_confirmed_name_with_false_payload_is_not_completed(self) -> None:
        task_id = self._make_running()
        self._write_trace(
            task_id, final_event="task.completed", final_payload={"summary": "x"},
        )
        trace = TraceStore(self.root / f"tr_{task_id}.jsonl", f"tr_{task_id}")
        trace.append(task_id=task_id, actor="system",
                     event="sandbox.destroy.confirmed",
                     payload={"cleanup": {"verified": False}})
        trace.seal(self.root / f"tr_{task_id}.anchor.json")
        self.assertEqual(self._run(), [])
        self.assertEqual(self.service.get_record(task_id).status,
                         TaskStatus.RUNNING)

    def test_confirmed_name_with_missing_verified_is_not_completed(self) -> None:
        task_id = self._make_running()
        self._write_trace(
            task_id, final_event="task.completed", final_payload={"summary": "x"},
        )
        trace = TraceStore(self.root / f"tr_{task_id}.jsonl", f"tr_{task_id}")
        trace.append(task_id=task_id, actor="system",
                     event="sandbox.destroy.confirmed",
                     payload={"cleanup": {}})
        trace.seal(self.root / f"tr_{task_id}.anchor.json")
        self.assertEqual(self._run(), [])
        self.assertEqual(self.service.get_record(task_id).status,
                         TaskStatus.RUNNING)

    def test_unsealed_or_unconfirmed_trace_is_not_completed(self) -> None:
        task_id = self._make_running()
        self._write_trace(task_id, final_event="task.completed",
                          final_payload={"summary": "x"}, anchor=False)
        self.assertEqual(self._run(), [])
        self.assertEqual(self.service.get_record(task_id).status,
                         TaskStatus.RUNNING)

    def test_legacy_destroyed_event_is_not_a_strict_completion(self) -> None:
        from app.trace import verify_trace_file
        task_id = self._make_running()
        path = self.root / f"tr_{task_id}.jsonl"
        trace = TraceStore(path, f"tr_{task_id}")
        trace.append(task_id=task_id, actor="system", event="task.created",
                     payload={})
        trace.append(task_id=task_id, actor="agent", event="task.completed",
                     payload={"summary": "legacy"})
        trace.append(task_id=task_id, actor="system", event="sandbox.destroyed",
                     payload={})
        trace.seal(self.root / f"tr_{task_id}.anchor.json")
        result = verify_trace_file(
            path, task_status="completed",
            anchor_path=self.root / f"tr_{task_id}.anchor.json",
            strict=True,
        )
        self.assertNotEqual(result["status"], "VERIFIED")
        self.assertFalse(result["ok"])
        self.assertEqual(self._run(), [])
        self.assertEqual(self.service.get_record(task_id).status,
                         TaskStatus.RUNNING)

    def test_db_finish_failure_then_recovery(self) -> None:
        task_id = self._make_running()
        with mock.patch.object(self.service.store, "finish",
                               side_effect=OSError("temporary db failure")):
            self.assertIsNone(self.service.finish(
                task_id, TaskStatus.COMPLETED, summary="done",
                worker_id="app",
            ))
        self.assertEqual(self.service.get_record(task_id).status,
                         TaskStatus.RUNNING)
        self._write_trace(task_id, final_event="task.completed",
                          final_payload={"summary": "done"})
        trace = TraceStore(self.root / f"tr_{task_id}.jsonl", f"tr_{task_id}")
        trace.append(task_id=task_id, actor="system",
                     event="sandbox.destroy.confirmed",
                     payload={"cleanup": {"verified": True}})
        trace.seal(self.root / f"tr_{task_id}.anchor.json")
        self.assertEqual(self._run(), [task_id])
        self.assertEqual(self.service.get_record(task_id).status,
                         TaskStatus.COMPLETED)

    def test_requeue_flag_does_not_disable_reconcile_loop(self) -> None:
        started = threading.Event()
        with mock.patch.dict(os.environ, {"REQUEUE_ON_STARTUP": "0"}), \
                mock.patch.object(maintenance, "_requeue_loop",
                                  side_effect=lambda: started.set()):
            maintenance._REQUEUE_STARTED = False
            maintenance._start_requeue_loop()
            self.assertTrue(started.wait(2.0))
        maintenance._REQUEUE_STARTED = False

    def test_pagination_covers_older_records(self) -> None:
        for index in range(3):
            self._make_running(f"tk{index}")
        maintenance._PAGE_CURSORS.clear()
        with mock.patch.object(settings, "TASK_SERVICE", self.service):
            first = list(maintenance._iter_task_pages(
                TaskStatus.RUNNING, page_size=2, max_pages=1,
            ))
            # A record can become terminal during the scan; the keyset cursor
            # must still reach the older record on the next pass.
            self.assertIsNotNone(self.service.finish(
                "tk1", TaskStatus.COMPLETED, worker_id="app",
            ))
            second = list(maintenance._iter_task_pages(
                TaskStatus.RUNNING, page_size=2, max_pages=1,
            ))
        self.assertEqual(len(first), 2)
        self.assertEqual([record.id for record in second], ["tk0"])
        self.assertEqual({record.id for record in first + second},
                         {"tk0", "tk1", "tk2"})

    def test_stale_worker_cannot_overwrite_new_owner(self) -> None:
        store1 = TaskStore(self.root / "race.db")
        store2 = TaskStore(self.root / "race.db")
        try:
            store1.create_task(task_id="tk", trace_id="tr", owner="u", prompt="x")
            store1.transition("tk", TaskStatus.RUNNING, worker_id="old")
            self.assertIsNone(store2.finish(
                "tk", TaskStatus.COMPLETED, expected_worker_id="new",
            ))
            done = store2.finish("tk", TaskStatus.COMPLETED,
                                 expected_worker_id="old")
            self.assertIsNotNone(done)
            self.assertEqual(done.status, TaskStatus.COMPLETED)
        finally:
            store1.close()
            store2.close()


if __name__ == "__main__":
    unittest.main()
