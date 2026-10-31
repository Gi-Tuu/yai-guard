"""Agent 循环的离线端到端测试：Mock 模型 + Fake 沙箱 + 真实策略 + 真实 trace。"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.agent.loop import run_agent  # noqa: E402
from app.agent.mock_model import MockModel  # noqa: E402
from app.policy import PolicyEngine  # noqa: E402
from app.sandbox.fake_backend import FakeSandbox  # noqa: E402
from app.trace import TraceStore, new_trace_id  # noqa: E402


class AgentLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.trace = TraceStore(pathlib.Path(self.tmp.name) / "trace.jsonl", new_trace_id())
        self.policy = PolicyEngine.from_file()
        self.sandbox = FakeSandbox()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run_demo(self, **kwargs):  # type: ignore[override]
        return run_agent(
            task="create a python project, run it, then try a dangerous command",
            model=kwargs.get("model", MockModel()),
            policy=self.policy,
            sandbox=self.sandbox,
            trace=self.trace,
            task_id="tk_test",
            approver=kwargs.get("approver", lambda decision, call: True),
        )

    def test_end_to_end_with_policy_and_trace(self) -> None:
        result = self._run_demo()
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.steps, 7)

        events = self.trace.read()
        types = [e["event"] for e in events]
        for expected in ("sandbox.created", "plan.created", "tool.requested",
                         "policy.decision", "sandbox.exec", "file.changed",
                         "task.completed", "sandbox.destroyed"):
            self.assertIn(expected, types, expected)

        denials = [e for e in events if e["event"] == "policy.decision"
                   and e["policy"]["effect"] == "deny"]
        self.assertTrue(denials, "should contain at least one real denial")
        rule_ids = {e["policy"]["rule_id"] for e in denials}
        self.assertIn("D-RM-ROOT", rule_ids)
        self.assertIn("D-CURL-PIPE-SH", rule_ids)

        approvals = [e for e in events if e["event"] == "approval.resolved"]
        self.assertTrue(approvals)
        self.assertTrue(approvals[0]["payload"]["approved"])

        ok, bad = self.trace.verify()
        self.assertTrue(ok)
        self.assertIsNone(bad)

    def test_denied_action_is_not_executed(self) -> None:
        self._run_demo()
        execs = [e for e in self.trace.read() if e["event"] == "sandbox.exec"]
        executed = [e["exec"]["argv"] for e in execs]
        self.assertNotIn("rm -rf /", executed)
        self.assertIn("python3 main.py", executed)

    def test_default_approver_rejects(self) -> None:
        result = self._run_demo(approver=lambda decision, call: False)
        self.assertEqual(result.status, "completed")
        approvals = [e for e in self.trace.read() if e["event"] == "approval.resolved"]
        self.assertTrue(approvals)
        self.assertFalse(approvals[0]["payload"]["approved"])

    def test_sandbox_is_destroyed(self) -> None:
        self._run_demo()
        self.assertNotIn("tk_test", self.sandbox._tasks)


if __name__ == "__main__":
    unittest.main()
