"""Sandbox readiness：daemon / runtime / image 前置检查。

P1-6 要求：显式 runsc 不可用时必须报错，不能静默降级到 runc。
"""

from __future__ import annotations

import os
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.sandbox.docker_backend import DockerError, DockerSandbox  # noqa: E402

IMAGE = "yaiguard/sandbox-py:1.0"
REQUIRE_DOCKER = os.environ.get("REQUIRE_DOCKER_TESTS") == "1"


def _docker_ok() -> bool:
    try:
        return DockerSandbox().daemon_status()["ok"]
    except Exception:  # noqa: BLE001
        return False


def _image_ok() -> bool:
    return _docker_ok() and DockerSandbox(image=IMAGE).image_status()["ok"]


class ReadinessTest(unittest.TestCase):
    def test_unreachable_docker_fails_explicitly(self) -> None:
        sandbox = DockerSandbox(docker_bin="definitely-not-a-docker-binary")
        status = sandbox.daemon_status()
        self.assertFalse(status["ok"])
        with self.assertRaises(DockerError):
            sandbox.ensure_ready()

    @unittest.skipUnless(_docker_ok() or REQUIRE_DOCKER, "docker daemon unavailable")
    def test_daemon_reports_runtimes(self) -> None:
        status = DockerSandbox().daemon_status()
        if REQUIRE_DOCKER:
            self.assertTrue(status["ok"], status)
        self.assertIn("runc", status["runtimes"])

    @unittest.skipUnless(_image_ok() or REQUIRE_DOCKER, "sandbox image unavailable")
    def test_requested_missing_runtime_fails_not_fallback(self) -> None:
        sandbox = DockerSandbox(runtime="runsc", image=IMAGE)
        daemon = sandbox.daemon_status()
        if REQUIRE_DOCKER:
            self.assertTrue(daemon["ok"], daemon)
        if "runsc" not in daemon["runtimes"]:
            with self.assertRaises(DockerError) as ctx:
                sandbox.ensure_ready()
            self.assertIn("runsc", str(ctx.exception))
        else:  # pragma: no cover - gVisor host
            self.assertTrue(sandbox.ensure_ready()["runtime"] == "runsc")

    @unittest.skipUnless(_image_ok() or REQUIRE_DOCKER, "sandbox image unavailable")
    def test_ensure_ready_ok_with_runc(self) -> None:
        info = DockerSandbox(runtime="runc", image=IMAGE).ensure_ready()
        if REQUIRE_DOCKER:
            self.assertTrue(info["daemon"]["ok"])
            self.assertEqual(info["runtime"], "runc")
            self.assertTrue(info["image"]["ok"])


class RemoteDestroyTest(unittest.TestCase):
    def test_remote_destroy_rejects_unverified(self) -> None:
        from app.sandbox.remote_backend import RemoteSandbox

        class BadRemote(RemoteSandbox):
            def _request(self, method, path, body=None, *, raw=False):
                return {"ok": True, "verified": False, "error": "still exists"}

        with self.assertRaises(RuntimeError):
            BadRemote("http://127.0.0.1:9000", "token").destroy("tk_x")


if __name__ == "__main__":
    unittest.main()
