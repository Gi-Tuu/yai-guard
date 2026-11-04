"""真实 Docker 集成测试：没有 daemon 或镜像时 skip（绝不 fail）。

覆盖：
- DockerSandbox 端到端（create → write_file → exec → get_file → destroy）
- M1 只读根 / M2 fork bomb / M3 零网络（直接用加固参数起一次性容器）

跑之前：
    docker build -f deploy/sandbox/Dockerfile -t yaiguard/sandbox-py:1.0 .
"""

from __future__ import annotations

import base64
import os
import pathlib
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

IMAGE = "yaiguard/sandbox-py:1.0"
RUNTIME = os.environ.get("TEST_SANDBOX_RUNTIME") or None
RUNTIME_ARGS = ["--runtime", RUNTIME] if RUNTIME else []
HARDENING = [
    "--network=none", "--read-only", "--cap-drop=ALL",
    "--security-opt=no-new-privileges:true", "--user=65534:65534",
]


def _run(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def _docker_available() -> bool:
    try:
        return _run(["docker", "info"], timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _image_available() -> bool:
    if not _docker_available():
        return False
    try:
        return _run(["docker", "image", "inspect", IMAGE], timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


DOCKER = _docker_available()
IMAGE_READY = _image_available()
SKIP_REASON = "docker daemon unavailable" if not DOCKER else f"image {IMAGE} not built"
if os.environ.get("REQUIRE_DOCKER_TESTS") == "1" and not IMAGE_READY:
    raise RuntimeError(f"REQUIRE_DOCKER_TESTS=1 but Docker/image unavailable: {SKIP_REASON}")


@unittest.skipUnless(IMAGE_READY, SKIP_REASON)
class DockerContainmentTest(unittest.TestCase):
    """M1/M2/M3：绕过策略层，直接验证内核/容器边界真的挡住了攻击。"""

    def test_m1_readonly_root_but_workspace_writable(self) -> None:
        result = _run([
            "docker", "run", "--rm", *RUNTIME_ARGS, *HARDENING,
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
            "--mount", "type=tmpfs,destination=/workspace,tmpfs-size=67108864,tmpfs-mode=1777",
            "--pids-limit=128", "--memory=256m", "--cpus=1",
            IMAGE, "sh", "-c",
            "rm -rf --no-preserve-root /; echo rm_exit=$?; echo ok > /workspace/x; echo write_exit=$?; cat /workspace/x",
        ])
        self.assertIn("Read-only file system", result.stderr)
        self.assertIn("write_exit=0", result.stdout)
        self.assertIn("ok", result.stdout)

    def test_m2_fork_bomb_is_absorbed_by_pids_limit(self) -> None:
        name = "yaig-test-m2"
        _run(["docker", "rm", "-f", name])
        created = _run([
            "docker", "run", "-d", "--name", name, *RUNTIME_ARGS,
            "--pids-limit=64", "--memory=128m", "--network=none",
            IMAGE, "sh", "-c", "b(){ b|b& }; b; sleep 10; echo after",
        ])
        self.assertEqual(created.returncode, 0, created.stderr)
        try:
            output = ""
            for _ in range(20):
                time.sleep(0.4)
                logs = _run(["docker", "logs", name])
                output = (logs.stdout + logs.stderr).lower()
                if "fork" in output:
                    break
            self.assertTrue(
                "cannot fork" in output or "resource temporarily unavailable" in output,
                output,
            )
            # 容器被 pids 限额吸收：没有 OOM，也没有把宿主拖垮
            self.assertNotIn("out of memory", output)
        finally:
            _run(["docker", "rm", "-f", name])

    def test_m3_network_is_none(self) -> None:
        script = (
            "import socket\n"
            "try:\n"
            "    socket.create_connection(('example.com', 443), timeout=3)\n"
            "    print('CONNECTED')\n"
            "except Exception as exc:\n"
            "    print('BLOCKED', type(exc).__name__)\n"
            "import os\n"
            "print('interfaces', sorted(os.listdir('/sys/class/net')))\n"
        )
        result = _run([
            "docker", "run", "--rm", *RUNTIME_ARGS, *HARDENING,
            "--mount", "type=tmpfs,destination=/workspace,tmpfs-size=67108864,tmpfs-mode=1777",
            IMAGE, "python3", "-c", script,
        ])
        self.assertIn("BLOCKED", result.stdout)
        self.assertIn("interfaces ['lo']", result.stdout)


@unittest.skipUnless(IMAGE_READY, SKIP_REASON)
class DockerSandboxBackendTest(unittest.TestCase):
    """通过真实后端跑一遍：验证 DockerSandbox 的文件与执行语义。"""

    def test_backend_end_to_end(self) -> None:
        from app.sandbox.docker_backend import DockerSandbox
        from app.sandbox.interface import Limits

        sandbox = DockerSandbox(image=IMAGE, runtime=RUNTIME)
        task_id = "tk_docker_test"
        try:
            sandbox.create(task_id, Limits(cpu=0.5, memory_mb=256, pids=64, timeout_s=30))
            source = b"print(1 + 1)\n"
            sandbox.put_file(task_id, "/workspace/main.py", source)
            roundtrip = sandbox.get_file(task_id, "/workspace/main.py")
            self.assertEqual(base64.b64decode(base64.b64encode(roundtrip)), source)

            result = sandbox.exec(task_id, "python3 main.py")
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.stdout.strip(), "2")

            listing = sandbox.list_files(task_id, "/workspace")
            self.assertIn("main.py", listing)

            denied = sandbox.exec(task_id, "rm -rf --no-preserve-root /")
            self.assertNotEqual(denied.exit_code, 0)
            self.assertIn("Read-only file system", denied.stderr)
        finally:
            sandbox.destroy(task_id)

    def test_destroy_is_verified_and_idempotent(self) -> None:
        from app.sandbox.docker_backend import DockerSandbox
        from app.sandbox.interface import Limits

        sandbox = DockerSandbox(image=IMAGE, runtime=RUNTIME)
        task_id = "tk_destroy_verify"
        sandbox.create(task_id, Limits(timeout_s=10))
        first = sandbox.destroy(task_id)
        self.assertTrue(first["verified"], first)
        second = sandbox.destroy(task_id)  # already absent: must still be verified
        self.assertTrue(second["verified"], second)

    def test_destroy_immediately_after_timeout_kill(self) -> None:
        """Regression: destroy must survive the rm/--rm race after a timeout kill."""
        from app.sandbox.docker_backend import DockerSandbox
        from app.sandbox.interface import Limits

        sandbox = DockerSandbox(image=IMAGE, runtime=RUNTIME)
        for index in range(20):
            task_id = f"tk_timeout_destroy_{index}"
            sandbox.create(task_id, Limits(cpu=0.5, memory_mb=256, pids=64,
                                           timeout_s=10))
            result = sandbox.exec(task_id, "sleep 30", timeout_s=1)
            self.assertTrue(result.timed_out, result)
            destroyed = sandbox.destroy(task_id)
            self.assertTrue(destroyed["verified"], destroyed)
            self.assertGreaterEqual(destroyed["attempts"], 1)


if __name__ == "__main__":
    unittest.main()

