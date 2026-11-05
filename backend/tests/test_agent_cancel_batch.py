"""Cancellation must stop the remaining tools in the same model response."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.agent.loop import run_agent  # noqa: E402
from app.agent.mock_model import ModelResponse  # noqa: E402
from app.policy import PolicyEngine, ToolCall  # noqa: E402
from app.sandbox.fake_backend import FakeSandbox  # noqa: E402
from app.trace import TraceStore  # noqa: E402


class SlowModel:
    def complete(self, messages, *, tools=None, model=None):
        time.sleep(1.2)
        return ModelResponse(content="late answer", finish_reason="stop")


class BatchModel:
    def complete(self, messages, *, tools=None, model=None):
        return ModelResponse(
            content="write two files then finish",
            tool_calls=[
                ToolCall("c1", "write_file", {"path": "/workspace/a", "content": "a"}),
                ToolCall("c2", "write_file", {"path": "/workspace/b", "content": "b"}),
                ToolCall("c3", "finish", {"summary": "done"}),
            ],
            finish_reason="tool_calls",
        )


class CancelAfterFirstWrite(FakeSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.cancel = threading.Event()

    def put_file(self, task_id, path, data):
        super().put_file(task_id, path, data)
        self.cancel.set()


class AgentCancelBatchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.trace = TraceStore(pathlib.Path(self.tmp.name) / "trace.jsonl", "tr_cancel_batch")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_cancel_during_batch_stops_remaining_tools(self) -> None:
        sandbox = CancelAfterFirstWrite()
        result = run_agent(
            task="write two files",
            model=BatchModel(),
            policy=PolicyEngine.from_file(),
            sandbox=sandbox,
            trace=self.trace,
            task_id="tk_cancel_batch",
            approver=lambda d, c: True,
            require_claims=False,
            should_cancel=sandbox.cancel.is_set,
            max_steps=2,
        )
        self.assertEqual(result.status, "cancelled")
        self.assertIn("/workspace/a", sandbox._files)
        self.assertNotIn("/workspace/b", sandbox._files)
        events = self.trace.read()
        self.assertTrue(any(e["event"] == "task.cancelled" for e in events))
        self.assertFalse(any(e["event"] == "task.completed" for e in events))

    def test_deadline_wins_over_shared_cancel_event(self) -> None:
        cancel_event = threading.Event()
        timer = threading.Timer(1.0, cancel_event.set)
        timer.start()
        try:
            result = run_agent(
                task="slow model",
                model=SlowModel(),
                policy=PolicyEngine.from_file(),
                sandbox=FakeSandbox(),
                trace=self.trace,
                task_id="tk_deadline",
                approver=lambda d, c: True,
                task_timeout_s=1,
                max_steps=2,
                should_cancel=cancel_event.is_set,
            )
        finally:
            timer.cancel()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "task_timeout")
        events = self.trace.read()
        self.assertTrue(any(e["event"] == "task.failed" for e in events))
        self.assertFalse(any(e["event"] == "task.cancelled" for e in events))


if __name__ == "__main__":
    unittest.main()
