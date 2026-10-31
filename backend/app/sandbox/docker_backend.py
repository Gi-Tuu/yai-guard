# 真实 Docker 后端：唯一允许调用 subprocess 的模块。
#
# 加固参数逐条对应 04 文档 §4.1；本模块只负责「怎么起容器/怎么执行」，
# 「能不能执行」由 PolicyEngine 决定（纵深防御，两层独立）。
#
# 静态检查约定：api/ agent/ policy/ 目录禁止出现 subprocess/os.system/eval/exec，
# 只有本文件（以及 controller 入口）例外。
from __future__ import annotations

import base64
import shlex
import subprocess
import time
from pathlib import Path

from .interface import ExecResult, Limits

SANDBOX_IMAGE = "yaiguard/sandbox-py:1.0"


class DockerError(RuntimeError):
    pass


def build_run_args(task_id: str, limits: Limits, *, image: str = SANDBOX_IMAGE,
                   runtime: str | None = None, network: str = "none") -> list[str]:
    """生成加固后的 `docker run` 参数（纯函数，可离线单测）。

    任何新增参数都必须先过 04 文档 §4.1 的「禁止清单」。
    """
    name = f"yaig-{task_id}"
    args = [
        "docker", "run", "-d", "--rm",
        "--name", name,
        "--network", network,
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--tmpfs", "/run:rw,noexec,nosuid,size=16m",
        "--mount", "type=tmpfs,destination=/workspace,tmpfs-size=268435456,tmpfs-mode=1777",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--security-opt=apparmor=docker-default",
        f"--pids-limit={limits.pids}",
        f"--memory={limits.memory_mb}m",
        f"--memory-swap={limits.memory_mb}m",
        f"--cpus={limits.cpu}",
        f"--ulimit", f"nofile={limits.nofile}:{limits.nofile}",
        "--user=65534:65534",
        "--ipc=none",
        "--workdir=/workspace",
        "--stop-timeout=5",
        f"--label=yaig.task={task_id}",
    ]
    if runtime:
        args += ["--runtime", runtime]
    args += [image, "sleep", "infinity"]
    return args


class DockerSandbox:
    def __init__(self, *, image: str = SANDBOX_IMAGE, runtime: str | None = None,
                 network: str = "none", docker_bin: str = "docker") -> None:
        self.image = image
        self.runtime = runtime
        self.network = network
        self.docker_bin = docker_bin
        self._limits = Limits()

    # ------------------------------------------------------------- lifecycle
    def create(self, task_id: str, limits: Limits) -> None:
        self._limits = limits
        args = build_run_args(task_id, limits, image=self.image, runtime=self.runtime, network=self.network)
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise DockerError(f"docker run failed: {result.stderr.strip()[:400]}")

    def destroy(self, task_id: str) -> None:
        subprocess.run([self.docker_bin, "rm", "-f", f"yaig-{task_id}"],
                       capture_output=True, text=True, timeout=30)

    # ------------------------------------------------------------------ files
    def put_file(self, task_id: str, path: str, data: bytes) -> None:
        # 用 base64 走 stdin，避免宿主临时文件与 docker cp 的路径歧义
        payload = base64.b64encode(data).decode("ascii")
        script = f"mkdir -p $(dirname {shlex.quote(path)}) && echo {payload} | base64 -d > {shlex.quote(path)}"
        self._run([self.docker_bin, "exec", "-i", f"yaig-{task_id}", "sh", "-c", script], input_text=None)

    def get_file(self, task_id: str, path: str, *, max_bytes: int = 1_048_576) -> bytes:
        out = self._run(
            [self.docker_bin, "exec", f"yaig-{task_id}", "sh", "-c",
             f"base64 -w0 {shlex.quote(path)} 2>/dev/null | head -c {max_bytes * 2}"],
            input_text=None,
        )
        try:
            return base64.b64decode(out.stdout.encode("ascii"))
        except Exception:
            return b""

    def list_files(self, task_id: str, path: str = "/workspace") -> list[str]:
        out = self._run([self.docker_bin, "exec", f"yaig-{task_id}", "sh", "-c",
                         f"find {shlex.quote(path)} -maxdepth 2 -type f -printf '%P\\n' 2>/dev/null"])
        return [line for line in out.stdout.splitlines() if line]

    # ------------------------------------------------------------------- exec
    def exec(self, task_id: str, command: str, *, timeout_s: int | None = None,
             cwd: str = "/workspace") -> ExecResult:
        limit = timeout_s or self._limits.timeout_s
        started = time.monotonic()
        args = [self.docker_bin, "exec", "-w", cwd, f"yaig-{task_id}", "sh", "-lc", command]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=limit)
            duration = int((time.monotonic() - started) * 1000)
            stdout, truncated = _truncate(result.stdout, self._limits.max_output_bytes)
            stderr, _ = _truncate(result.stderr, self._limits.max_output_bytes)
            return ExecResult(stdout=stdout, stderr=stderr, exit_code=result.returncode,
                              duration_ms=duration, truncated=truncated)
        except subprocess.TimeoutExpired:
            subprocess.run([self.docker_bin, "kill", f"yaig-{task_id}"], capture_output=True, timeout=20)
            return ExecResult(stderr=f"timeout after {limit}s", exit_code=124,
                              duration_ms=int((time.monotonic() - started) * 1000),
                              timed_out=True, killed_reason="wall_clock")

    def host_fingerprint(self) -> dict:
        try:
            kernel = Path("/proc/version").read_text(encoding="utf-8", errors="replace")[:120]
        except OSError:
            kernel = "unknown"
        return {"kernel": kernel}

    # ---------------------------------------------------------------- helpers
    def _run(self, args: list[str], input_text: str | None = None) -> subprocess.CompletedProcess:
        result = subprocess.run(args, capture_output=True, text=True, timeout=60,
                                input=input_text)
        if result.returncode != 0 and "No such file" not in result.stderr:
            # 读取不存在文件是正常路径，其它错误向上抛
            raise DockerError(result.stderr.strip()[:400])
        return result


def _truncate(text: str, limit: int) -> tuple[str, bool]:
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", "replace") + "\n...[truncated]", True
