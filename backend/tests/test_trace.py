"""Trace 存储的离线单测：追加、哈希链校验、篡改检测、脱敏、续写。"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.trace import TraceStore, new_trace_id  # noqa: E402


class TraceStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.tmp.name) / "trace.jsonl"
        self.store = TraceStore(self.path, new_trace_id())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_append_and_verify(self) -> None:
        for i in range(3):
            self.store.append(task_id="tk_1", actor="system", event="agent.step",
                              payload={"step": i})
        events = self.store.read()
        self.assertEqual([e["seq"] for e in events], [1, 2, 3])
        ok, bad = self.store.verify()
        self.assertTrue(ok)
        self.assertIsNone(bad)
        self.assertEqual(events[0]["prev_hash"], "sha256:" + "0" * 64)

    def test_tampering_is_detected(self) -> None:
        for _ in range(3):
            self.store.append(task_id="tk_1", actor="system", event="agent.step", payload={"x": 1})
        lines = self.path.read_text(encoding="utf-8").splitlines()
        tampered = json.loads(lines[1])
        tampered["payload"]["x"] = 999
        lines[1] = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        ok, bad = self.store.verify()
        self.assertFalse(ok)
        self.assertEqual(bad, 2)

    def test_redaction_happens_before_hashing(self) -> None:
        secret = "sk-supersecret123456789"
        self.store.append(
            task_id="tk_1",
            actor="system",
            event="agent.step",
            payload={"headers": f"Bearer {secret}", "env": f"VULTR_INFERENCE_API_KEY={secret}"},
        )
        raw = self.path.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        self.assertIn("***", raw)
        ok, _ = self.store.verify()
        self.assertTrue(ok)

    def test_export_is_parseable_jsonl(self) -> None:
        self.store.append(task_id="tk_1", actor="user", event="task.created", payload={"task": "demo"})
        exported = self.store.export_jsonl()
        parsed = [json.loads(line) for line in exported.splitlines() if line.strip()]
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["event"], "task.created")

    def test_resume_continues_chain(self) -> None:
        self.store.append(task_id="tk_1", actor="system", event="agent.step", payload={"n": 1})
        reopened = TraceStore(self.path, self.store.trace_id)
        reopened.append(task_id="tk_1", actor="system", event="agent.step", payload={"n": 2})
        events = reopened.read()
        self.assertEqual([e["seq"] for e in events], [1, 2])
        self.assertTrue(reopened.verify()[0])

    def test_unknown_event_type_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.append(task_id="tk_1", actor="system", event="not.an.event", payload={})


if __name__ == "__main__":
    unittest.main()
