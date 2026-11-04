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


if __name__ == "__main__":
    unittest.main()
