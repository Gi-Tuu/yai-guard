"""红队语料回归：逐条断言策略效果，失败时把 note 与真实 rule 一起打出来。

语料是可增长的资产：新增绕过手法先加一条 case，再补规则。
"""

from __future__ import annotations

import json
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.policy import Effect, PolicyEngine, ToolCall  # noqa: E402

CORPUS = pathlib.Path(__file__).resolve().parent / "corpus" / "bypass.jsonl"

EFFECTS = {
    "allow": Effect.ALLOW,
    "deny": Effect.DENY,
    "approval": Effect.REQUIRE_APPROVAL,
}


def load_corpus() -> list[dict]:
    cases: list[dict] = []
    for line in CORPUS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(json.loads(line))
    return cases


class PolicyCorpusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = PolicyEngine.from_file()
        cls.cases = load_corpus()

    def test_corpus_has_enough_cases(self) -> None:
        self.assertGreaterEqual(len(self.cases), 30)
        for key in ("command", "expect", "note"):
            self.assertTrue(all(key in case for case in self.cases), key)

    def test_every_case_matches_expected_effect(self) -> None:
        failures: list[str] = []
        for case in self.cases:
            decision = self.engine.evaluate(
                ToolCall("c1", "run_command", {"command": case["command"]})
            )
            expect = EFFECTS[case["expect"]]
            if decision.effect is not expect:
                failures.append(
                    f"command={case['command']!r}\n"
                    f"  expect={case['expect']} actual={decision.effect.value} "
                    f"rule={decision.rule_id}\n  note={case['note']}"
                )
                continue
            if case.get("rule") and decision.rule_id != case["rule"]:
                failures.append(
                    f"command={case['command']!r}\n"
                    f"  expect rule={case['rule']} actual={decision.rule_id}\n"
                    f"  note={case['note']}"
                )
        self.assertEqual(failures, [], "\n\n".join(failures))

    def test_main_demo_commands_are_never_denied(self) -> None:
        """演示主线（正常路径）：任何一条 deny 都是误拦回归。攻击尝试不在此列。"""
        demo = [
            "ls /workspace",
            "cat main.py",
            "python3 main.py",
            "python3 main.py > out.txt",
            "pip install numpy",
        ]
        for command in demo:
            decision = self.engine.evaluate(ToolCall("c1", "run_command", {"command": command}))
            self.assertIsNot(decision.effect, Effect.DENY, f"{command} -> {decision.rule_id}")

    def test_every_denial_has_explainable_rule_and_reason(self) -> None:
        for case in self.cases:
            if case["expect"] != "deny":
                continue
            decision = self.engine.evaluate(
                ToolCall("c1", "run_command", {"command": case["command"]})
            )
            self.assertTrue(decision.rule_id)
            self.assertTrue(decision.reason)


if __name__ == "__main__":
    unittest.main()
