"""Containment Lab 探针测试（使用可编程 fake 后端，不依赖 Docker）。"""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.containment import run_containment  # noqa: E402
from app.sandbox.fake_backend import FakeSandbox  # noqa: E402
from app.sandbox.interface import ExecResult  # noqa: E402


class ProbeSandbox(FakeSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.created = False
        self.destroyed = False

    def create(self, task_id, limits):  # type: ignore[override]
        super().create(task_id, limits)
        self.created = True

    def destroy(self, task_id):  # type: ignore[override]
        self.destroyed = True
        return {"ok": True, "verified": True, "backend": "fake", "container": f"yaig-{task_id}"}

    def exec(self, task_id, command, *, timeout_s=None, cwd="/workspace"):  # type: ignore[override]
        if "open('/etc/hostname'" in command:
            return ExecResult(stderr="OSError: [Errno 30] Read-only file system", exit_code=1)
        if "create_connection" in command:
            return ExecResult(stderr="OSError: [Errno 101] Network is unreachable", exit_code=1)
        if "os.path.exists('/var/run/docker.sock')" in command:
            return ExecResult(stdout="SOCKET=False\n", exit_code=0)
        if command.strip() == "env":
            return ExecResult(stdout="PATH=/usr/local/bin\nHOME=/tmp\n", exit_code=0)
        return ExecResult(stdout="", exit_code=0)


class ContainmentTest(unittest.TestCase):
    def test_all_probes_pass_and_cleanup(self) -> None:
        sandbox = ProbeSandbox()
        result = run_containment(sandbox, "ct_test")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["summary"], "4/4 contained")
        self.assertTrue(result["cleanup"]["verified"])
        self.assertTrue(sandbox.destroyed)

    def test_secret_leak_fails(self) -> None:
        class Leaky(ProbeSandbox):
            def exec(self, task_id, command, *, timeout_s=None, cwd="/workspace"):
                if command.strip() == "env":
                    return ExecResult(stdout="SANDBOX_TOKEN=leaked\n", exit_code=0)
                return super().exec(task_id, command, timeout_s=timeout_s, cwd=cwd)

        result = run_containment(Leaky(), "ct_leak")
        self.assertFalse(result["ok"])
        self.assertFalse(next(p for p in result["probes"] if p["name"] == "no_secrets")["passed"])


if __name__ == "__main__":
    unittest.main()
