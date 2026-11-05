"""Claim-Evidence 绑定：finish 的 claim 必须引用真实 sandbox.exec 序号。"""
from __future__ import annotations

import json
import pathlib
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.agent.loop import run_agent  # noqa: E402
from app.agent.mock_model import ModelResponse  # noqa: E402
from app.policy import PolicyEngine, ToolCall  # noqa: E402
from app.sandbox.fake_backend import FakeSandbox  # noqa: E402
from app.sandbox.interface import ExecResult  # noqa: E402
from app.trace import TraceStore  # noqa: E402


def _last_evidence(messages: list[dict]) -> dict:
    for message in reversed(messages):
        text = message.get("content", "")
        match = re.search(r"<sandbox_output>\s*(\{.*?\})\s*</sandbox_output>", text, re.S)
        if not match:
            continue
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if "evidence_seq" in data:
            return data
    raise AssertionError("no evidence block in conversation: " + repr(messages[-1])[:300])


class ClaimModel:
    """先跑一条命令，再读取 tool 输出里的 evidence 完成。"""

    def __init__(self) -> None:
        self.step = 0

    def complete(self, messages, *, tools=None, model=None):
        self.step += 1
        if self.step == 1:
            return ModelResponse(
                content="run a command",
                tool_calls=[ToolCall("c1", "run_command", {"command": "echo hi"})],
                finish_reason="tool_calls",
            )
        evidence = _last_evidence(messages)
        return ModelResponse(
            content="finished",
            tool_calls=[ToolCall("c2", "finish", {
                "summary": "echo ran with evidence",
                "claims": [{
                    "text": "echo exited 0",
                    "evidence_seq": evidence["evidence_seq"],
                    "evidence_argv": evidence["evidence_argv"],
                    "evidence_stdout_sha256": evidence["evidence_stdout_sha256"],
                }],
            })],
            finish_reason="tool_calls",
        )


class InvalidClaimModel(ClaimModel):
    def complete(self, messages, *, tools=None, model=None):
        self.step += 1
        if self.step == 1:
            return ModelResponse(
                content="run a command",
                tool_calls=[ToolCall("c1", "run_command", {"command": "echo hi"})],
                finish_reason="tool_calls",
            )
        return ModelResponse(
            content="finish with fake evidence",
            tool_calls=[ToolCall("c2", "finish", {
                "summary": "claim without evidence",
                "claims": [{
                    "text": "echo exited 0",
                    "evidence_seq": 999999,
                    "evidence_argv": "echo hi",
                    "evidence_stdout_sha256": "0" * 64,
                }],
            })],
            finish_reason="tool_calls",
        )


class MissingClaimModel(ClaimModel):
    def complete(self, messages, *, tools=None, model=None):
        self.step += 1
        if self.step == 1:
            return ModelResponse(
                content="run a command",
                tool_calls=[ToolCall("c1", "run_command", {"command": "echo hi"})],
                finish_reason="tool_calls",
            )
        return ModelResponse(
            content="finish without claims",
            tool_calls=[ToolCall("c2", "finish", {"summary": "no claims"})],
            finish_reason="tool_calls",
        )


class NoFinishModel(ClaimModel):
    """Executes a command, then returns a plain answer without calling finish."""

    def __init__(self) -> None:
        self.step = 0

    def complete(self, messages, *, tools=None, model=None):
        self.step += 1
        if self.step == 1:
            return ModelResponse(
                content="run a command",
                tool_calls=[ToolCall("c1", "run_command", {"command": "echo hi"})],
                finish_reason="tool_calls",
            )
        return ModelResponse(content="finished without claims", finish_reason="stop")


class CorrectionModel(ClaimModel):
    """Ignores finish once, then follows the evidence correction."""

    def __init__(self) -> None:
        self.step = 0

    def complete(self, messages, *, tools=None, model=None):
        self.step += 1
        if self.step == 1:
            return ModelResponse(
                content="run a command",
                tool_calls=[ToolCall("c1", "run_command", {"command": "echo hi"})],
                finish_reason="tool_calls",
            )
        if self.step == 2:
            return ModelResponse(content="finished without claims", finish_reason="stop")
        evidence = _last_evidence(messages)
        return ModelResponse(
            content="finished after correction",
            tool_calls=[ToolCall("c2", "finish", {
                "summary": "echo ran with evidence",
                "claims": [{
                    "text": "echo exited 0",
                    "evidence_seq": evidence["evidence_seq"],
                    "evidence_argv": evidence["evidence_argv"],
                    "evidence_stdout_sha256": evidence["evidence_stdout_sha256"],
                }],
            })],
            finish_reason="tool_calls",
        )


class FailingSandbox(FakeSandbox):
    def exec(self, task_id, command, *, timeout_s=None, cwd="/workspace"):
        return ExecResult(
            stdout="", stderr="tests failed", exit_code=1, duration_ms=1,
            timed_out=False, truncated=False, killed_reason=None,
        )


class ClaimEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.trace = TraceStore(pathlib.Path(self.tmp.name) / "trace.jsonl", "tr_claim")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_valid_claim_is_bound_to_real_seq(self) -> None:
        result = run_agent(
            task="run echo hi",
            model=ClaimModel(),
            policy=PolicyEngine.from_file(),
            sandbox=FakeSandbox(),
            trace=self.trace,
            task_id="tk_claim",
            approver=lambda d, c: True,
            require_claims=True,
            max_steps=4,
        )
        self.assertEqual(result.status, "completed")
        events = self.trace.read()
        completed = next(e for e in events if e["event"] == "task.completed")
        self.assertTrue(completed["payload"]["claims_bound"])
        exec_seq = next(e["seq"] for e in events if e["event"] == "sandbox.exec")
        self.assertEqual(completed["payload"]["claims"][0]["evidence_seq"], exec_seq)

    def test_failed_command_claim_is_binding_only_not_semantic(self) -> None:
        # This pins the documented boundary: a claim may cite a real failed
        # command. Binding proves identity, not that "tests passed" is true.
        result = run_agent(
            task="run a failing command",
            model=ClaimModel(),
            policy=PolicyEngine.from_file(),
            sandbox=FailingSandbox(),
            trace=self.trace,
            task_id="tk_failed_claim",
            approver=lambda d, c: True,
            require_claims=True,
            max_steps=4,
        )
        self.assertEqual(result.status, "completed")
        completed = next(e for e in self.trace.read() if e["event"] == "task.completed")
        self.assertTrue(completed["payload"]["claims_bound"])
        self.assertEqual(completed["payload"]["claims"][0]["evidence_exit_code"], 1)
        self.assertIn("binding-only", completed["payload"]["claim_semantics"])

    def test_fake_seq_is_rejected_until_max_steps(self) -> None:
        result = run_agent(
            task="run echo hi",
            model=InvalidClaimModel(),
            policy=PolicyEngine.from_file(),
            sandbox=FakeSandbox(),
            trace=self.trace,
            task_id="tk_bad",
            approver=lambda d, c: True,
            require_claims=True,
            max_steps=3,
        )
        self.assertEqual(result.status, "failed")
        events = self.trace.read()
        self.assertFalse(any(e["event"] == "task.completed" for e in events))
        self.assertTrue(any(e["event"] == "task.failed" for e in events))

    def test_missing_claims_is_rejected_when_command_ran(self) -> None:
        result = run_agent(
            task="run echo hi",
            model=MissingClaimModel(),
            policy=PolicyEngine.from_file(),
            sandbox=FakeSandbox(),
            trace=self.trace,
            task_id="tk_missing",
            approver=lambda d, c: True,
            require_claims=True,
            max_steps=2,
        )
        self.assertEqual(result.status, "failed")
        self.assertFalse(any(e["event"] == "task.completed" for e in self.trace.read()))

    def test_plain_answer_without_finish_is_rejected(self) -> None:
        result = run_agent(
            task="run echo hi",
            model=NoFinishModel(),
            policy=PolicyEngine.from_file(),
            sandbox=FakeSandbox(),
            trace=self.trace,
            task_id="tk_no_finish",
            approver=lambda d, c: True,
            require_claims=True,
            max_steps=3,
        )
        self.assertEqual(result.status, "failed")
        self.assertFalse(any(e["event"] == "task.completed" for e in self.trace.read()))

    def test_correction_then_finish_completes(self) -> None:
        result = run_agent(
            task="run echo hi",
            model=CorrectionModel(),
            policy=PolicyEngine.from_file(),
            sandbox=FakeSandbox(),
            trace=self.trace,
            task_id="tk_correction",
            approver=lambda d, c: True,
            require_claims=True,
            max_steps=4,
        )
        self.assertEqual(result.status, "completed")
        completed = next(e for e in self.trace.read() if e["event"] == "task.completed")
        self.assertTrue(completed["payload"]["claims_required"])
        self.assertTrue(completed["payload"]["claims_bound"])

if __name__ == "__main__":
    unittest.main()
