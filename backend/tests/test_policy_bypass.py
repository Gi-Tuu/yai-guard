"""Regression tests for policy path/redirect bypasses."""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.policy import Effect, PolicyEngine, ToolCall  # noqa: E402


class PolicyBypassTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = PolicyEngine.from_file()

    def cmd(self, command: str):
        return self.engine.evaluate(ToolCall("c1", "run_command", {"command": command}))

    def test_relative_traversal_is_denied(self) -> None:
        decision = self.cmd("cat foo/../../etc/passwd")
        self.assertIs(decision.effect, Effect.DENY)

    def test_redirect_without_whitespace_is_denied(self) -> None:
        decision = self.cmd("echo hi>/tmp/pwn")
        self.assertIs(decision.effect, Effect.DENY)


if __name__ == "__main__":
    unittest.main()
