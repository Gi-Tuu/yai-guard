"""Maintenance evidence should preserve the real interruption reason."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import maintenance, settings, task_state  # noqa: E402
from app.trace import TraceStore  # noqa: E402


class MaintenanceEvidenceTest(unittest.TestCase):
    def test_requeue_reuses_existing_state_object(self) -> None:
        existing = task_state.TaskState(
            task_id="tk_requeue", trace_id="tr_requeue", task="x", owner="u",
        )
        existing.cancel_event.set()
        record = SimpleNamespace(
            id="tk_requeue", trace_id="tr_requeue", prompt="x", owner="u",
            cancel_requested=False,
        )

        class Service:
            enabled = True

            def queued_tasks(self):
                return [record]

        class Executor:
            def __init__(self):
                self.submitted = []

            def submit(self, fn, state):
                self.submitted.append(state)

        executor = Executor()
        with task_state._LOCK:
            task_state.TASKS.clear()
            task_state.TASKS["tk_requeue"] = existing
        with task_state._DISPATCH_LOCK:
            task_state._DISPATCHED_QUEUED.clear()
        try:
            with mock.patch.object(settings, "TASK_SERVICE", Service()), \
                    mock.patch.object(maintenance.quiesce, "is_quiesced",
                                      return_value=False), \
                    mock.patch.object(task_state, "_TASK_EXECUTOR", executor), \
                    mock.patch.object(task_state, "_reserve_queue_slot",
                                      return_value=True):
                summary = maintenance._requeue_queued_tasks()
            self.assertEqual(summary["requeued"], ["tk_requeue"])
            self.assertIs(executor.submitted[0], existing)
            self.assertTrue(executor.submitted[0].cancel_event.is_set())
        finally:
            with task_state._LOCK:
                task_state.TASKS.clear()
            with task_state._DISPATCH_LOCK:
                task_state._DISPATCHED_QUEUED.clear()

    def test_cleanup_unverified_is_reconciled_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            traces = pathlib.Path(tmp)
            trace_id = "tr_cleanup"
            trace = TraceStore(traces / f"{trace_id}.jsonl", trace_id)
            trace.append(task_id="tk", actor="system", event="task.created", payload={})
            trace.append(task_id="tk", actor="agent", event="task.completed",
                         payload={"summary": "done"})
            trace.append(task_id="tk", actor="system",
                         event="sandbox.destroy.unverified",
                         payload={"cleanup": {"verified": False}})
            record = SimpleNamespace(
                id="tk", trace_id=trace_id, error="lease_expired",
            )
            calls: list[tuple] = []

            class Service:
                enabled = True

                def interrupted_tasks(self):
                    return [record]

                def reconcile_terminal(self, task_id, status, **kwargs):
                    calls.append((task_id, status, kwargs))
                    return None

            with mock.patch.object(settings, "TRACES", traces), \
                    mock.patch.object(settings, "TASK_SERVICE", Service()):
                recorded = maintenance._record_interrupted_events()

            self.assertEqual(recorded, ["tk"])
            self.assertEqual(calls[0][0], "tk")
            self.assertEqual(calls[0][1].value, "failed")
            self.assertEqual(calls[0][2]["error"], "cleanup_unverified")

    def test_interrupted_event_uses_record_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            traces = pathlib.Path(tmp)
            trace_id = "tr_reason"
            trace = TraceStore(traces / f"{trace_id}.jsonl", trace_id)
            trace.append(task_id="tk", actor="system", event="task.created", payload={})
            record = SimpleNamespace(
                id="tk", trace_id=trace_id, error="lease_expired",
            )

            class Service:
                enabled = True

                def interrupted_tasks(self):
                    return [record]

            with mock.patch.object(settings, "TRACES", traces), \
                    mock.patch.object(settings, "TASK_SERVICE", Service()), \
                    mock.patch.object(settings, "TRACE_ED25519_KEY", None):
                recorded = maintenance._record_interrupted_events()

            self.assertEqual(recorded, ["tk"])
            events = trace.read()
            interrupted = next(e for e in events if e["event"] == "task.interrupted")
            self.assertEqual(interrupted["payload"]["reason"], "lease_expired")


if __name__ == "__main__":
    unittest.main()
