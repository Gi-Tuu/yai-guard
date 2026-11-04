"""Trace 存储的离线单测：追加、哈希链校验、篡改检测、脱敏、续写。"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.trace import TraceStore, new_trace_id, verify_file  # noqa: E402
from app.trace.store import verify_trace_file  # noqa: E402


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

    # ----------------------------------------------- 独立文件校验（API 端点用）
    def test_verify_file_matches_store_verify(self) -> None:
        for _ in range(2):
            self.store.append(task_id="tk_1", actor="system", event="agent.step", payload={"x": 1})
        self.assertEqual(verify_file(self.path), (True, None))

    def test_verify_file_missing_file(self) -> None:
        self.assertEqual(verify_file(pathlib.Path(self.tmp.name) / "nope.jsonl"), (False, None))

    def test_verify_file_detects_tampering(self) -> None:
        for _ in range(2):
            self.store.append(task_id="tk_1", actor="user", event="task.created", payload={"task": "x"})
        lines = self.path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["actor"] = "attacker"
        lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(verify_file(self.path), (False, 1))


class TraceIntegrityTest(unittest.TestCase):
    """P1-4：区分 chain / sequence / completeness / anchor / HMAC。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.trace = self.dir / "trace.jsonl"
        self.anchor = self.dir / "trace.anchor.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make(self, *, completed: bool = True, anchor: bool = True, hmac_key: str = "k"):
        store = TraceStore(self.trace, "tr_integrity")
        store.append(task_id="tk", actor="system", event="task.created", payload={})
        if completed:
            store.append(task_id="tk", actor="agent", event="task.completed", payload={})
        store.append(task_id="tk", actor="system", event="sandbox.destroy.confirmed", payload={})
        if anchor:
            store.seal(self.anchor, hmac_key=hmac_key)
        return store

    def test_valid_complete_trace(self) -> None:
        self._make()
        result = verify_trace_file(self.trace, task_status="completed",
                                   anchor_path=self.anchor, hmac_key="k")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["chain_ok"])
        self.assertTrue(result["sequence_ok"])
        self.assertTrue(result["completeness_ok"])
        self.assertTrue(result["anchor_ok"])
        self.assertTrue(result["authentic"])

    def test_empty_file_is_invalid(self) -> None:
        self.trace.write_text("", encoding="utf-8")
        result = verify_trace_file(self.trace)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "empty")

    def test_deleted_last_event_fails_completeness_and_anchor(self) -> None:
        self._make()
        lines = self.trace.read_text(encoding="utf-8").splitlines()
        self.trace.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
        result = verify_trace_file(self.trace, task_status="completed",
                                   anchor_path=self.anchor, hmac_key="k")
        self.assertFalse(result["ok"])
        self.assertFalse(result["completeness_ok"])
        self.assertFalse(result["anchor_ok"])

    def test_missing_terminal_event_fails_completeness(self) -> None:
        self._make(completed=False)
        result = verify_trace_file(self.trace, task_status="completed",
                                   anchor_path=self.anchor, hmac_key="k")
        self.assertTrue(result["chain_ok"])
        self.assertTrue(result["sequence_ok"])
        self.assertFalse(result["completeness_ok"])
        self.assertFalse(result["ok"])

    def test_sequence_gap_is_detected(self) -> None:
        self._make()
        lines = self.trace.read_text(encoding="utf-8").splitlines()
        second = json.loads(lines[1])
        second["seq"] = 99
        lines[1] = json.dumps(second, sort_keys=True, separators=(",", ":"))
        self.trace.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = verify_trace_file(self.trace)
        self.assertFalse(result["sequence_ok"])
        self.assertFalse(result["ok"])

    def test_anchor_mismatch_after_append(self) -> None:
        store = self._make()
        store.append(task_id="tk", actor="system", event="agent.step", payload={})
        result = verify_trace_file(self.trace, task_status="completed",
                                   anchor_path=self.anchor, hmac_key="k")
        self.assertTrue(result["chain_ok"])
        self.assertFalse(result["anchor_ok"])
        self.assertFalse(result["ok"])

    def test_hmac_tamper_is_detected(self) -> None:
        self._make()
        anchor = json.loads(self.anchor.read_text(encoding="utf-8"))
        anchor["hmac"] = "0" * 64
        self.anchor.write_text(json.dumps(anchor), encoding="utf-8")
        result = verify_trace_file(self.trace, task_status="completed",
                                   anchor_path=self.anchor, hmac_key="k")
        self.assertFalse(result["authentic"])
        self.assertFalse(result["ok"])

    # ------------------------------------------------------ strict mode (P0-E)
    def test_strict_missing_anchor_is_incomplete(self) -> None:
        self._make(anchor=False)
        result = verify_trace_file(self.trace, task_status="completed", strict=True)
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertFalse(result["ok"])

    def test_strict_running_task_is_pending(self) -> None:
        TraceStore(self.trace, "tr_running").append(
            task_id="tk", actor="system", event="task.created", payload={})
        result = verify_trace_file(self.trace, task_status="running", strict=True)
        self.assertEqual(result["status"], "PENDING")
        self.assertFalse(result["ok"])

    def test_strict_hmac_missing_is_not_verified(self) -> None:
        self._make(hmac_key="")
        result = verify_trace_file(self.trace, task_status="completed",
                                   anchor_path=self.anchor, hmac_key="k", strict=True)
        self.assertFalse(result["authentic"])
        self.assertEqual(result["status"], "FAILED")
        self.assertFalse(result["ok"])

    def test_strict_valid_trace_is_verified(self) -> None:
        self._make()
        result = verify_trace_file(self.trace, task_status="completed",
                                   anchor_path=self.anchor, hmac_key="k", strict=True)
        self.assertEqual(result["status"], "VERIFIED")
        self.assertTrue(result["ok"])


    def test_strict_destroyed_without_confirmation_is_incomplete(self) -> None:
        store = TraceStore(self.trace, "tr_legacy")
        store.append(task_id="tk", actor="system", event="task.created", payload={})
        store.append(task_id="tk", actor="agent", event="task.completed", payload={})
        store.append(task_id="tk", actor="system", event="sandbox.destroyed", payload={})
        store.seal(self.anchor, hmac_key="k")
        result = verify_trace_file(self.trace, task_status="completed",
                                   anchor_path=self.anchor, hmac_key="k", strict=True)
        self.assertEqual(result["status"], "INCOMPLETE")

    def test_strict_cancelled_trace_is_verified(self) -> None:
        store = TraceStore(self.trace, "tr_cancelled")
        store.append(task_id="tk", actor="system", event="task.created", payload={})
        store.append(task_id="tk", actor="system", event="task.cancelled", payload={})
        store.append(task_id="tk", actor="system", event="sandbox.destroy.confirmed",
                     payload={"cleanup": {"verified": True}})
        store.seal(self.anchor, hmac_key="k")
        result = verify_trace_file(self.trace, task_status="cancelled",
                                   anchor_path=self.anchor, hmac_key="k", strict=True)
        self.assertEqual(result["status"], "VERIFIED")

if __name__ == "__main__":
    unittest.main()
