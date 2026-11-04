"""Evidence Bundle 构建与独立校验测试。"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.trace.bundle import build_bundle, verify_bundle_dict  # noqa: E402
from app.trace.store import TraceStore  # noqa: E402


class BundleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.trace = self.dir / "trace.jsonl"
        self.anchor = self.dir / "trace.anchor.json"
        self.store = TraceStore(self.trace, "tr_bundle")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _make(self, *, confirmed: bool = True, anchor: bool = True) -> None:
        self.store.append(task_id="tk", actor="user", event="task.created", payload={})
        self.store.append(task_id="tk", actor="system", event="sandbox.created", payload={
            "runtime": "runc",
            "limits": {"memory_mb": 512},
            "sandbox": {"backend": "docker", "runtime": "runc",
                        "container_id": "abc123", "image_id": "sha256:img"},
        })
        self.store.append(task_id="tk", actor="sandbox", event="sandbox.exec",
                          execution={"argv": "echo hi", "exit_code": 0,
                                     "stdout_sha256": "a", "stderr_sha256": "b"})
        self.store.append(task_id="tk", actor="agent", event="task.completed", payload={"summary": "ok"})
        if confirmed:
            self.store.append(task_id="tk", actor="system", event="sandbox.destroy.confirmed",
                              payload={"cleanup": {"verified": True, "container": "abc123"}})
        if anchor:
            self.store.seal(self.anchor, hmac_key="k")

    def test_verified_bundle(self) -> None:
        self._make()
        bundle = build_bundle(self.trace, anchor_path=self.anchor, hmac_key="k")
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "VERIFIED")
        self.assertTrue(result["ok"])
        self.assertTrue(result["content_ok"])
        self.assertTrue(result["signature_ok"])
        self.assertTrue(result["fully_verified"])
        self.assertEqual(result["verification_level"], "signed")

    def test_tampered_event_fails(self) -> None:
        self._make()
        bundle = build_bundle(self.trace, anchor_path=self.anchor)
        bundle["trace"]["events"][2]["exec"]["exit_code"] = 1
        result = verify_bundle_dict(bundle)
        self.assertEqual(result["status"], "FAILED")

    def test_missing_cleanup_is_incomplete(self) -> None:
        self._make(confirmed=False)
        bundle = build_bundle(self.trace, anchor_path=self.anchor)
        result = verify_bundle_dict(bundle)
        self.assertEqual(result["status"], "INCOMPLETE")

    def test_missing_anchor_is_incomplete(self) -> None:
        self._make(anchor=False)
        bundle = build_bundle(self.trace)
        result = verify_bundle_dict(bundle)
        self.assertEqual(result["status"], "INCOMPLETE")

    def test_missing_sandbox_fields_is_incomplete(self) -> None:
        self.store.append(task_id="tk", actor="user", event="task.created", payload={})
        self.store.append(task_id="tk", actor="system", event="sandbox.created", payload={})
        self.store.append(task_id="tk", actor="system", event="sandbox.exec",
                          execution={"argv": "echo hi", "exit_code": 0})
        self.store.append(task_id="tk", actor="agent", event="task.completed", payload={})
        self.store.append(task_id="tk", actor="system", event="sandbox.destroy.confirmed",
                          payload={"cleanup": {"verified": True}})
        self.store.seal(self.anchor)
        bundle = build_bundle(self.trace, anchor_path=self.anchor)
        result = verify_bundle_dict(bundle)
        self.assertEqual(result["status"], "INCOMPLETE")

    def test_bundle_sandbox_metadata_must_match_events(self) -> None:
        self._make()
        bundle = build_bundle(self.trace, anchor_path=self.anchor, hmac_key="k")
        bundle["sandbox"]["container_id"] = "tampered"
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "FAILED")
        self.assertFalse(result["sandbox_ok"])

    def test_bundle_execution_metadata_must_match_events(self) -> None:
        self._make()
        bundle = build_bundle(self.trace, anchor_path=self.anchor, hmac_key="k")
        bundle["execution"]["results"][0]["exit_code"] = 1
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "FAILED")
        self.assertFalse(result["execution_ok"])

    def test_bundle_task_status_must_match_events(self) -> None:
        self._make()
        bundle = build_bundle(self.trace, anchor_path=self.anchor, hmac_key="k")
        bundle["task"]["status"] = "failed"
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "FAILED")

    def test_bundle_final_hash_must_match_last_event(self) -> None:
        self._make()
        bundle = build_bundle(self.trace, anchor_path=self.anchor, hmac_key="k")
        bundle["trace"]["final_hash"] = "sha256:" + "0" * 64
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "FAILED")
        self.assertFalse(result["final_hash_ok"])

    def test_anchor_trace_id_must_match_bundle(self) -> None:
        self._make()
        bundle = build_bundle(self.trace, anchor_path=self.anchor, hmac_key="k")
        bundle["trace"]["anchor"]["trace_id"] = "tr_other"
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "FAILED")

    def test_unsigned_bundle_is_content_only(self) -> None:
        self._make()
        bundle = build_bundle(self.trace, anchor_path=self.anchor)
        result = verify_bundle_dict(bundle)
        self.assertEqual(result["status"], "VERIFIED")
        self.assertTrue(result["content_ok"])
        self.assertIsNone(result["signature_ok"])
        self.assertFalse(result["fully_verified"])
        self.assertEqual(result["verification_level"], "content_only")

    def test_anchor_event_count_mismatch_fails(self) -> None:
        self._make()
        bundle = build_bundle(self.trace, anchor_path=self.anchor, hmac_key="k")
        bundle["trace"]["anchor"]["events"] = 999
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "anchor_event_count_mismatch")

    def test_anchor_final_hash_mismatch_fails(self) -> None:
        self._make()
        bundle = build_bundle(self.trace, anchor_path=self.anchor, hmac_key="k")
        bundle["trace"]["anchor"]["final_hash"] = "sha256:" + "0" * 64
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "anchor_final_hash_mismatch")

    def test_bundle_sandbox_backend_mismatch_fails(self) -> None:
        self._make()
        bundle = build_bundle(self.trace, anchor_path=self.anchor, hmac_key="k")
        bundle["sandbox"]["backend"] = "fake"
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "sandbox_backend_mismatch")

    def test_hmac_key_requires_signed_anchor(self) -> None:
        self._make()
        bundle = build_bundle(self.trace, anchor_path=self.anchor)
        bundle["trace"]["anchor"].pop("hmac", None)
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "hmac_missing")
        self.assertFalse(result["signature_ok"])

if __name__ == "__main__":
    unittest.main()
