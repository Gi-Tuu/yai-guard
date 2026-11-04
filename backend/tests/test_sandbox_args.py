"""Docker 加固参数的离线单测：不启动容器，只检查参数契约。"""

from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.sandbox.docker_backend import build_run_args  # noqa: E402
from app.sandbox.interface import Limits  # noqa: E402


class DockerRunArgsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.args = build_run_args("tk_1", Limits())
        self.joined = " ".join(self.args)

    def test_isolation_defaults(self) -> None:
        self.assertIn("--network none", self.joined)
        self.assertIn("--read-only", self.joined)
        self.assertIn("--cap-drop=ALL", self.joined)
        self.assertIn("--security-opt=no-new-privileges:true", self.joined)
        self.assertIn("--user=65534:65534", self.joined)
        self.assertIn("--ipc=none", self.joined)
        self.assertIn("--pids-limit=128", self.joined)
        self.assertIn("--memory=512m", self.joined)
        self.assertIn("--memory-swap=512m", self.joined)
        self.assertIn("--cpus=1.0", self.joined)
        self.assertIn("--tmpfs /tmp:rw,noexec,nosuid,size=64m", self.joined)

    def test_forbidden_flags_are_absent(self) -> None:
        for forbidden in ("--privileged", "--pid=host", "--network=host",
                          "--userns=host", "docker.sock", "-v /"):
            self.assertNotIn(forbidden, self.joined, forbidden)

    def test_runtime_can_be_switched(self) -> None:
        args = build_run_args("tk_1", Limits(), runtime="runsc")
        self.assertIn("--runtime", args)
        self.assertIn("runsc", args)

    def test_limits_are_mapped(self) -> None:
        args = build_run_args("tk_1", Limits(cpu=0.5, memory_mb=256, pids=64, nofile=128))
        joined = " ".join(args)
        self.assertIn("--cpus=0.5", joined)
        self.assertIn("--memory=256m", joined)
        self.assertIn("--pids-limit=64", joined)
        self.assertIn("--ulimit nofile=128:128", joined)


if __name__ == "__main__":
    unittest.main()
