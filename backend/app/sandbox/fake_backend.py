# 离线假沙箱：不启动任何容器，用于单元测试与「无 key、无 Docker」的本地演示。
#
# 它只模拟最少的命令语义（python 脚本、ls、cat、rm、curl、sleep），目的是验证
# Agent 循环、策略闸门与 trace，而不是替代真实沙箱。真实 containment 必须在
# Docker/gVisor 里跑（见 docker_backend.py 与 docs/04 §10）。
from __future__ import annotations

import posixpath
import re
import time
from datetime import datetime, timezone

from .interface import ExecResult, Limits

_PRINT_RE = re.compile(r"print\s*\(\s*(.+?)\s*\)", re.DOTALL)


class FakeSandbox:
    def __init__(self) -> None:
        self.limits = Limits()
        self._files: dict[str, bytes] = {"/workspace/.keep": b""}
        self._tasks: set[str] = set()
        self._killed: dict[str, str] = {}
        self.host = {
            "vm": "fake-app-vm",
            "kernel": "6.8.0-fake",
            "hostname": "yai-app",
            "docker": "fake",
        }

    # ------------------------------------------------------------- lifecycle
    def create(self, task_id: str, limits: Limits) -> None:
        self.limits = limits
        self._tasks.add(task_id)
        self._container_id = f"fake-{task_id}"
        self._image_id = "fake-image"

    def sandbox_info(self) -> dict:
        return {"backend": "fake", "runtime": "fake",
                "container_id": getattr(self, "_container_id", None),
                "image_id": getattr(self, "_image_id", None)}

    def destroy(self, task_id: str) -> dict:
        self._tasks.discard(task_id)
        self._killed.pop(task_id, None)
        return {"ok": True, "verified": True, "backend": "fake", "container": f"yaig-{task_id}"}

    def list_containers(self) -> list[dict]:
        return []

    def host_fingerprint(self) -> dict:
        return dict(self.host, sandbox_hostname=f"yaig-sandbox-{next(iter(self._tasks), 'idle')}")

    def host_snapshot(self) -> dict:
        """静态假数据：让 UI 的「宿主完好」面板在离线模式也能渲染。"""
        return {
            "source": "fake",
            "df_inodes": "Filesystem Inodes IUsed IFree IUse% /  1048576 1200 1047376 1%",
            "etc_files": 1180,
            "uptime": "up 3 days, 4:05",
            "proc_count": 42,
            "connections": 0,
            "captured_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }

    # ------------------------------------------------------------------ files
    def put_file(self, task_id: str, path: str, data: bytes) -> None:
        norm = posixpath.normpath(path)
        if not norm.startswith("/workspace/"):
            raise PermissionError(f"write outside workspace: {norm}")
        self._files[norm] = data

    def get_file(self, task_id: str, path: str, *, max_bytes: int = 1_048_576,
                 allow_missing: bool = True) -> bytes:
        norm = posixpath.normpath(path)
        data = self._files.get(norm)
        if data is None:
            if allow_missing:
                return b""
            raise FileNotFoundError(path)
        return data[:max_bytes]

    def list_files(self, task_id: str, path: str = "/workspace") -> list[str]:
        prefix = posixpath.normpath(path).rstrip("/") + "/"
        return sorted(p[len(prefix):] for p in self._files if p.startswith(prefix))

    # ------------------------------------------------------------------- exec
    def exec(self, task_id: str, command: str, *, timeout_s: int | None = None, cwd: str = "/workspace") -> ExecResult:
        started = time.monotonic()
        limit = timeout_s or self.limits.timeout_s

        if "sleep" in command:
            match = re.search(r"sleep\s+(\d+)", command)
            if match and int(match.group(1)) > limit:
                return self._result("", "timeout: command exceeded limit", 124,
                                    started, timed_out=True, killed_reason="wall_clock")

        if re.search(r"\brm\s+-[A-Za-z]*[rf][A-Za-z]*\s+/(\s|$|\*)", command):
            return self._result(
                "",
                "rm: cannot remove '/bin': Read-only file system\n"
                "rm: cannot remove '/etc': Read-only file system\n"
                "rm: cannot remove '/usr': Read-only file system\n",
                1, started,
            )

        if ":(){ :|:& };:" in command or "while true" in command:
            return self._result(
                "", "bash: fork: retry: Resource temporarily unavailable\n", 1, started,
                killed_reason="pids_limit",
            )

        if "curl" in command or "wget" in command:
            return self._result("", "curl: (6) Could not resolve host\n", 6, started)

        if command.strip().startswith(("uname",)):
            return self._result(f"yaig-sandbox-{task_id}\n", "", 0, started)

        if command.strip().startswith("ls"):
            files = self.list_files(task_id)
            return self._result("\n".join(files) + ("\n" if files else ""), "", 0, started)

        if command.strip().startswith("cat "):
            target = command.split(None, 1)[1].strip()
            data = self.get_file(task_id, target)
            return self._result(data.decode("utf-8", "replace"), "", 0, started)

        if "python" in command:
            code = self._load_python_source(task_id, command)
            match = _PRINT_RE.search(code or "")
            out = ""
            if match:
                expr = match.group(1).strip().strip("'\"")
                try:
                    out = str(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 - 仅解析简单字面量
                except Exception:
                    out = expr
            return self._result(out + ("\n" if out else ""), "", 0, started)

        return self._result("", "", 0, started)

    # ---------------------------------------------------------------- helpers
    def _load_python_source(self, task_id: str, command: str) -> str:
        parts = command.split()
        for token in reversed(parts):
            if token.endswith(".py"):
                target = token if token.startswith("/") else "/workspace/" + token
                return self.get_file(task_id, target).decode("utf-8", "replace")
        return command

    @staticmethod
    def _result(stdout: str, stderr: str, code: int, started: float, *,
                timed_out: bool = False, killed_reason: str | None = None) -> ExecResult:
        return ExecResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=code,
            duration_ms=int((time.monotonic() - started) * 1000),
            timed_out=timed_out,
            killed_reason=killed_reason,
        )
