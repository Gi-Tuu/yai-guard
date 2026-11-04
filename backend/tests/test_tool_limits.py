"""Hard caps on model-controlled tool arguments."""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.agent.tools import execute_tool  # noqa: E402
from app.policy import ToolCall  # noqa: E402
from app.sandbox.fake_backend import FakeSandbox  # noqa: E402
from app.sandbox.interface import ExecResult, Limits  # noqa: E402


class RecordingSandbox(FakeSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.seen_timeout = None

    def exec(self, task_id, command, *, timeout_s=None, cwd="/workspace"):
        self.seen_timeout = timeout_s
        return ExecResult(stdout="", stderr="", exit_code=0)


class ToolLimitTest(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = RecordingSandbox()
        self.sandbox.create("tk", Limits())

    def test_command_timeout_is_clamped_to_policy_cap(self) -> None:
        result = execute_tool(
            ToolCall("c", "run_command", {"command": "echo hi", "timeout_s": 999999}),
            self.sandbox,
            "tk",
            max_command_timeout=3,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(self.sandbox.seen_timeout, 3)

    def test_oversized_write_is_rejected_before_sandbox(self) -> None:
        result = execute_tool(
            ToolCall("c", "write_file", {"path": "/workspace/big.txt", "content": "x" * 200_000}),
            self.sandbox,
            "tk",
            max_output_bytes=8192,
        )
        self.assertFalse(result["ok"])
        self.assertIn("limit", result["error"])


    def test_missing_read_is_a_structured_error(self) -> None:
        result = execute_tool(
            ToolCall("c", "read_file", {"path": "/workspace/does-not-exist.txt"}),
            self.sandbox,
            "tk",
        )
        self.assertFalse(result["ok"])
        self.assertIn("read failed", result["error"])

if __name__ == "__main__":
    unittest.main()
