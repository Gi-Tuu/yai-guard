# 沙箱后端抽象：上层（Agent/Controller）只依赖这个窄接口。
# 这样 Docker / gVisor / microVM / 一次性实例可以互换，而不改 Agent 代码。
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Limits:
    cpu: float = 1.0
    memory_mb: int = 512
    pids: int = 128
    nofile: int = 512
    timeout_s: int = 120
    max_output_bytes: int = 8192

    def to_dict(self) -> dict:
        return {
            "cpus": self.cpu,
            "memory_mb": self.memory_mb,
            "pids": self.pids,
            "nofile": self.nofile,
            "timeout_s": self.timeout_s,
        }


@dataclass
class ExecResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    duration_ms: int = 0
    timed_out: bool = False
    truncated: bool = False
    killed_reason: str | None = None

    def to_exec_dict(self) -> dict:
        """给 trace 用：只存指纹与统计，不存原文。"""
        return {
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "stdout_bytes": len(self.stdout.encode("utf-8")),
            "stderr_bytes": len(self.stderr.encode("utf-8")),
            "stdout_sha256": hashlib.sha256(self.stdout.encode("utf-8")).hexdigest(),
            "stderr_sha256": hashlib.sha256(self.stderr.encode("utf-8")).hexdigest(),
            "truncated": self.truncated,
            "timed_out": self.timed_out,
            "killed_reason": self.killed_reason,
        }


class SandboxBackend(Protocol):
    """所有后端必须实现的最小生命周期。"""

    def create(self, task_id: str, limits: Limits) -> None: ...

    def exec(self, task_id: str, command: str, *, timeout_s: int | None = None, cwd: str = "/workspace") -> ExecResult: ...

    def put_file(self, task_id: str, path: str, data: bytes) -> None: ...

    def get_file(self, task_id: str, path: str, *, max_bytes: int = 1_048_576) -> bytes: ...

    def list_files(self, task_id: str, path: str = "/workspace") -> list[str]: ...

    def destroy(self, task_id: str) -> None: ...

    def host_fingerprint(self) -> dict: ...

    def host_snapshot(self) -> dict: ...
