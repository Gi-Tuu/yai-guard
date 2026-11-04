"""Maintenance evidence should preserve the real interruption reason."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import maintenance, settings  # noqa: E402
from app.trace import TraceStore  # noqa: E402


class MaintenanceEvidenceTest(unittest.TestCase):
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
