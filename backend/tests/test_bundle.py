"""Evidence Bundle 构建与独立校验测试。"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.trace.bundle import _claims_check, build_bundle, verify_bundle_dict  # noqa: E402
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
        self.assertEqual(result["status"], "CONTENT_VERIFIED")
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


    def test_bundle_cleanup_metadata_must_match_event(self) -> None:
        self._make()
        bundle = build_bundle(self.trace, anchor_path=self.anchor, hmac_key="k")
        bundle["lifecycle"]["destroy"]["cleanup"]["verified"] = False
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "FAILED")

    def test_destroyed_without_confirmed_is_incomplete(self) -> None:
        self.store.append(task_id="tk", actor="user", event="task.created", payload={})
        self.store.append(task_id="tk", actor="system", event="sandbox.created", payload={
            "runtime": "runc",
            "sandbox": {"backend": "docker", "runtime": "runc",
                        "container_id": "abc123", "image_id": "sha256:img"},
        })
        self.store.append(task_id="tk", actor="system", event="sandbox.exec",
                          execution={"argv": "echo hi", "exit_code": 0})
        self.store.append(task_id="tk", actor="agent", event="task.completed", payload={})
        self.store.append(task_id="tk", actor="system", event="sandbox.destroyed",
                          payload={"cleanup": {"verified": True}})
        self.store.seal(self.anchor)
        bundle = build_bundle(self.trace, anchor_path=self.anchor)
        result = verify_bundle_dict(bundle)
        self.assertEqual(result["status"], "INCOMPLETE")

    def test_policy_metadata_must_match_events(self) -> None:
        self.store.append(task_id="tk", actor="user", event="task.created", payload={})
        self.store.append(task_id="tk", actor="system", event="sandbox.created", payload={
            "runtime": "runc",
            "sandbox": {"backend": "docker", "runtime": "runc",
                        "container_id": "abc123", "image_id": "sha256:img"},
        })
        self.store.append(task_id="tk", actor="policy", event="policy.decision",
                          payload={"tool": "run_command"},
                          policy={"effect": "deny", "rule_id": "D-TEST",
                                  "reason": "test rule", "risk": "high"})
        self.store.append(task_id="tk", actor="sandbox", event="sandbox.exec",
                          execution={"argv": "echo hi", "exit_code": 0})
        self.store.append(task_id="tk", actor="agent", event="task.completed", payload={})
        self.store.append(task_id="tk", actor="system", event="sandbox.destroy.confirmed",
                          payload={"cleanup": {"verified": True}})
        self.store.seal(self.anchor, hmac_key="k")
        bundle = build_bundle(self.trace, anchor_path=self.anchor, hmac_key="k")
        bundle["policy"]["decisions"][0]["rule_id"] = "D-TAMPERED"
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "policy_metadata_mismatch")

    def test_approval_metadata_must_match_events(self) -> None:
        self.store.append(task_id="tk", actor="user", event="task.created", payload={})
        self.store.append(task_id="tk", actor="system", event="sandbox.created", payload={
            "runtime": "runc",
            "sandbox": {"backend": "docker", "runtime": "runc",
                        "container_id": "abc123", "image_id": "sha256:img"},
        })
        self.store.append(task_id="tk", actor="system", event="approval.resolved",
                          payload={"call_id": "call_1", "approved": True})
        self.store.append(task_id="tk", actor="sandbox", event="sandbox.exec",
                          execution={"argv": "echo hi", "exit_code": 0})
        self.store.append(task_id="tk", actor="agent", event="task.completed", payload={})
        self.store.append(task_id="tk", actor="system", event="sandbox.destroy.confirmed",
                          payload={"cleanup": {"verified": True}})
        self.store.seal(self.anchor, hmac_key="k")
        bundle = build_bundle(self.trace, anchor_path=self.anchor, hmac_key="k")
        bundle["approvals"][0]["payload"]["approved"] = False
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "approval_metadata_mismatch")

    def test_claim_evidence_must_cite_real_exec_seq(self) -> None:
        self.store.append(task_id="tk", actor="user", event="task.created", payload={})
        self.store.append(task_id="tk", actor="system", event="sandbox.created", payload={
            "runtime": "runc",
            "sandbox": {"backend": "docker", "runtime": "runc",
                        "container_id": "abc123", "image_id": "sha256:img"},
        })
        self.store.append(task_id="tk", actor="sandbox", event="sandbox.exec",
                          execution={"argv": "echo hi", "exit_code": 0,
                                     "stdout_sha256": "abc"})
        seq = self.store.read()[-1]["seq"]
        self.store.append(task_id="tk", actor="agent", event="task.completed", payload={
            "summary": "echo ran",
            "claims": [{"text": "echo exited 0", "evidence_seq": seq,
                        "evidence_argv": "echo hi", "evidence_stdout_sha256": "abc"}],
            "claims_verified": True,
            "claim_binding": "argv+sha256",
        })
        self.store.append(task_id="tk", actor="system", event="sandbox.destroy.confirmed",
                          payload={"cleanup": {"verified": True}})
        self.store.seal(self.anchor, hmac_key="k")
        bundle = build_bundle(self.trace, anchor_path=self.anchor, hmac_key="k")
        result = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(result["status"], "VERIFIED")
        self.assertTrue(result["claims_ok"])

        bundle["trace"]["events"][3]["payload"]["claims"][0]["evidence_seq"] = 999
        # Event 3 is task.completed in this fixture; its hash no longer matches the chain.
        tampered = verify_bundle_dict(bundle, hmac_key="k")
        self.assertEqual(tampered["status"], "FAILED")


    def test_claim_binding_rejects_wrong_argv_or_digest(self) -> None:
        events = [
            {"event": "sandbox.exec", "seq": 3,
             "exec": {"argv": "echo hi", "stdout_sha256": "abc"}},
            {"event": "task.completed", "payload": {
                "claims": [{"text": "echo exited 0", "evidence_seq": 3,
                            "evidence_argv": "echo hi", "evidence_stdout_sha256": "abc"}],
                "claims_verified": True,
                "claim_binding": "argv+sha256",
            }},
        ]
        self.assertTrue(_claims_check(events))
        events[1]["payload"]["claims"][0]["evidence_argv"] = "rm -rf /"
        self.assertFalse(_claims_check(events))

    def test_claims_required_without_claims_fails(self) -> None:
        self.store.append(task_id="tk", actor="user", event="task.created", payload={})
        self.store.append(task_id="tk", actor="system", event="sandbox.created", payload={
            "runtime": "runc",
            "sandbox": {"backend": "docker", "runtime": "runc",
                        "container_id": "abc123", "image_id": "sha256:img"},
        })
        self.store.append(task_id="tk", actor="system", event="sandbox.exec",
                          execution={"argv": "echo hi", "exit_code": 0})
        self.store.append(task_id="tk", actor="agent", event="task.completed",
                          payload={"summary": "no claims", "claims_required": True})
        self.store.append(task_id="tk", actor="system", event="sandbox.destroy.confirmed",
                          payload={"cleanup": {"verified": True, "container": "abc123"}})
        self.store.seal(self.anchor)
        bundle = build_bundle(self.trace, anchor_path=self.anchor)
        result = verify_bundle_dict(bundle)
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(result["reason"], "claim_evidence_mismatch")

    def test_ed25519_signed_bundle_requires_pinned_key_for_verified(self) -> None:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private = Ed25519PrivateKey.generate()
        public = private.public_key()
        self._make(anchor=False)
        self.store.seal(self.anchor, ed25519_private_key=private)
        bundle = build_bundle(self.trace, anchor_path=self.anchor)
        result = verify_bundle_dict(bundle, public_key=public)
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["signature_alg"], "ed25519")
        self.assertTrue(result["ed25519_ok"])

        unpinned = verify_bundle_dict(bundle)
        self.assertEqual(unpinned["status"], "CONTENT_VERIFIED")
        self.assertIsNone(unpinned["signature_ok"])

        other = Ed25519PrivateKey.generate().public_key()
        wrong = verify_bundle_dict(bundle, public_key=other)
        self.assertEqual(wrong["status"], "FAILED")
        self.assertEqual(wrong["reason"], "ed25519_mismatch")

    def test_cancelled_bundle_without_execution_is_content_verified(self) -> None:
        self.store.append(task_id="tk", actor="user", event="task.created", payload={})
        self.store.append(task_id="tk", actor="system", event="sandbox.created", payload={
            "runtime": "runc",
            "sandbox": {"backend": "docker", "runtime": "runc",
                        "container_id": "abc123", "image_id": "sha256:img"},
        })
        self.store.append(task_id="tk", actor="system", event="task.cancelled",
                          payload={"reason": "cancel_requested"})
        self.store.append(task_id="tk", actor="system", event="sandbox.destroy.confirmed",
                          payload={"cleanup": {"verified": True, "container": "abc123"}})
        self.store.seal(self.anchor)
        bundle = build_bundle(self.trace, anchor_path=self.anchor)
        result = verify_bundle_dict(bundle)
        self.assertEqual(result["status"], "CONTENT_VERIFIED")
        self.assertTrue(result["execution_ok"])

    def test_interrupted_bundle_without_cleanup_is_content_verified(self) -> None:
        self.store.append(task_id="tk", actor="user", event="task.created", payload={})
        self.store.append(task_id="tk", actor="system", event="task.interrupted",
                          payload={"reason": "app_restart"})
        self.store.seal(self.anchor)
        bundle = build_bundle(self.trace, anchor_path=self.anchor)
        result = verify_bundle_dict(bundle)
        self.assertEqual(result["status"], "CONTENT_VERIFIED")
        self.assertIsNone(result["cleanup_ok"])
        self.assertTrue(result["sandbox_ok"])

if __name__ == "__main__":
    unittest.main()
