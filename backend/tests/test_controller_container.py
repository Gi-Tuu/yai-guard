"""Controller + Docker 真实集成测试。

需要：
- 已构建 yaiguard/controller:test 与 yaiguard/sandbox-py:1.0；
- controller 容器已启动，CONTROLLER_URL（如 http://127.0.0.1:19000）与
  CONTROLLER_TOKEN 已设置。

CI 中由 docker-integration job 提供；本地未设置 CONTROLLER_URL 时跳过。
设置 REQUIRE_DOCKER_TESTS=1 时不允许跳过，缺依赖直接失败。
"""

from __future__ import annotations

import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.sandbox.interface import Limits  # noqa: E402
from app.sandbox.remote_backend import RemoteSandbox  # noqa: E402
from app.agent.loop import run_agent  # noqa: E402
from app.agent.mock_model import MockModel  # noqa: E402
from app.policy import PolicyEngine  # noqa: E402
from app.trace import TraceStore  # noqa: E402

CONTROLLER_URL = os.environ.get("CONTROLLER_URL", "")
CONTROLLER_TOKEN = os.environ.get("CONTROLLER_TOKEN", "")
REQUIRE = os.environ.get("REQUIRE_DOCKER_TESTS") == "1"
IMAGE = "yaiguard/sandbox-py:1.0"


def _docker_has(name: str) -> bool:
    proc = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"],
                          capture_output=True, text=True, timeout=20)
    return name in proc.stdout.split()


class ControllerIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not CONTROLLER_URL:
            if REQUIRE:
                raise RuntimeError("REQUIRE_DOCKER_TESTS=1 but CONTROLLER_URL is not set")
            raise unittest.SkipTest("CONTROLLER_URL not set")
        cls.sandbox = RemoteSandbox(CONTROLLER_URL, CONTROLLER_TOKEN)

    def setUp(self) -> None:
        self.task_id = "tk_it_" + uuid.uuid4().hex[:8]
        self.sandbox.create(self.task_id, Limits(timeout_s=10))

    def tearDown(self) -> None:
        self.sandbox.destroy(self.task_id)

    def test_lifecycle_and_isolation(self) -> None:
        self.sandbox.put_file(self.task_id, "/workspace/hello.txt", b"p0-evidence")
        self.assertEqual(self.sandbox.get_file(self.task_id, "/workspace/hello.txt"), b"p0-evidence")

        result = self.sandbox.exec(self.task_id, 'python3 -c "print(6*7)"')
        self.assertEqual(result.stdout.strip(), "42")
        self.assertEqual(result.exit_code, 0)

        hostname = self.sandbox.exec(self.task_id, "uname -n").stdout.strip()
        self.assertNotEqual(hostname, socket.gethostname(), "must not be the app host")

        root_write = self.sandbox.exec(self.task_id, "rm -rf --no-preserve-root /")
        self.assertIn("Read-only file system", root_write.stderr)
        self.assertNotEqual(root_write.exit_code, 0)

        network = self.sandbox.exec(
            self.task_id,
            "python3 -c \"import socket; socket.create_connection(('example.com',443),2)\"",
        )
        self.assertNotEqual(network.exit_code, 0)

        self.sandbox.destroy(self.task_id)
        self.assertFalse(_docker_has(f"yaig-{self.task_id}"), "container must be destroyed")

    def test_timeout_is_killed(self) -> None:
        result = self.sandbox.exec(self.task_id, "sleep 30", timeout_s=1)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.killed_reason, "wall_clock")
        self.assertLess(result.duration_ms, 10_000)

    def test_container_name_is_destroyed_after_teardown(self) -> None:
        self.sandbox.destroy(self.task_id)
        self.assertFalse(_docker_has(f"yaig-{self.task_id}"))

    def test_rejected_approval_is_not_executed(self) -> None:
        """完整链路：Agent → Policy → Approval(拒绝) → 不执行 → Trace。"""
        trace_dir = pathlib.Path(tempfile.mkdtemp(prefix="yaig-approval-"))
        trace = TraceStore(trace_dir / "trace.jsonl", "tr_integration")
        agent_task_id = self.task_id + "a"
        result = run_agent(
            task="create main.py, run it, then try rm -rf /workspace/tmp",
            model=MockModel(),
            policy=PolicyEngine.from_file(),
            sandbox=self.sandbox,
            trace=trace,
            task_id=agent_task_id,
            approver=lambda decision, call: False,
        )
        self.assertEqual(result.status, "completed")
        executed = [ev.get("exec", {}).get("argv") for ev in trace.read()
                    if ev["event"] == "sandbox.exec"]
        self.assertNotIn("rm -rf /workspace/tmp", executed)
        self.assertIn("python3 main.py", executed)
        self.assertTrue(trace.verify()[0])


if __name__ == "__main__":
    unittest.main()
