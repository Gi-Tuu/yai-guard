"""Deterministic fuzz regressions: malformed input must never verify or crash."""
from __future__ import annotations

import json
import pathlib
import random
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.policy import Effect, PolicyEngine, ToolCall  # noqa: E402
from app.trace.store import TraceStore, verify_trace_file  # noqa: E402


class PolicyFuzzTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = PolicyEngine.from_file()

    def test_random_commands_never_raise_and_always_decide(self) -> None:
        rng = random.Random(20261104)
        alphabet = "abcdefghijklmnopqrstuvwxyz0123456789 /\\\"'$;&|><*?()[]{}:.-_"
        for _ in range(500):
            command = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
            decision = self.engine.evaluate(ToolCall("c1", "run_command",
                                                     {"command": command}))
            self.assertIsInstance(decision.effect, Effect)

    def test_known_dangerous_inputs_are_denied_after_fuzz(self) -> None:
        for command in ("rm -rf /", "rm -rf /*", "rm -rf /etc", "echo x>/tmp/pwn",
                        "cat ../../etc/passwd", ":(){ :|:& };:"):
            decision = self.engine.evaluate(ToolCall("c1", "run_command",
                                                     {"command": command}))
            self.assertIs(decision.effect, Effect.DENY, command)


class TraceFuzzTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.trace = self.dir / "trace.jsonl"
        self.anchor = self.dir / "trace.anchor.json"
        store = TraceStore(self.trace, "tr_fuzz")
        store.append(task_id="tk", actor="user", event="task.created", payload={})
        store.append(task_id="tk", actor="system", event="task.completed", payload={})
        store.append(task_id="tk", actor="system", event="sandbox.destroy.confirmed",
                     payload={"cleanup": {"verified": True}})
        store.seal(self.anchor)
        self.original = self.trace.read_text(encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mutated_trace_never_verifies(self) -> None:
        rng = random.Random(20261104)
        lines = self.original.splitlines()
        for _ in range(120):
            mutated = list(lines)
            index = rng.randrange(len(mutated))
            text = mutated[index]
            position = rng.randrange(len(text))
            replacement = rng.choice("{}[]\"x0123456789")
            if replacement == text[position]:
                continue
            mutated[index] = text[:position] + replacement + text[position + 1:]
            self.assertNotEqual(mutated[index], text)
            self.trace.write_text("\n".join(mutated) + "\n", encoding="utf-8")
            result = verify_trace_file(
                self.trace, task_status="completed",
                anchor_path=self.anchor, strict=True,
            )
            self.assertFalse(result["ok"], result)
            self.assertNotEqual(result["status"], "VERIFIED", result)

    def test_random_garbage_never_verifies(self) -> None:
        for _ in range(60):
            self.trace.write_text(json.dumps({"random": "x" * 100}) + "\n",
                                  encoding="utf-8")
            result = verify_trace_file(self.trace, task_status="completed",
                                       anchor_path=self.anchor, strict=True)
            self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
