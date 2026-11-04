# 真实 Docker 后端：唯一允许调用 subprocess 的模块。
#
# 加固参数逐条对应 04 文档 §4.1；本模块只负责「怎么起容器/怎么执行」，
# 「能不能执行」由 PolicyEngine 决定（纵深防御，两层独立）。
#
# 静态检查约定：api/ agent/ policy/ 目录禁止出现 subprocess/os.system/eval/exec，
# 只有本文件（以及 controller 入口）例外。
from __future__ import annotations

import base64
import json
import os
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .interface import ExecResult, Limits

SANDBOX_IMAGE = "yaiguard/sandbox-py:1.0"


@dataclass
class BoundedResult:
    stdout: bytes
    stderr: bytes
    exit_code: int
    duration_ms: int
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    killed_reason: str | None

    @property
    def truncated(self) -> bool:
        return self.stdout_truncated or self.stderr_truncated


def _kill_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - Windows CI
            proc.kill()
    except (OSError, ProcessLookupError):  # pragma: no cover
        try:
            proc.kill()
        except OSError:
            pass


def run_bounded(
    args: list[str],
    *,
    timeout_s: float,
    stdout_cap: int,
    stderr_cap: int,
    total_cap: int,
) -> BoundedResult:
    """有界流式执行：不会把无界输出读进宿主机内存。

    为什么必须这样做：`subprocess.run(capture_output=True)` 会先把全部输出收进内存，
    容器内存限制保护不了宿主机 Python 进程。这里用两个 reader 线程持续 drain，
    只保留每路上限内的字节；总输出超过 total_cap 立即 kill 进程组。
    """
    started = time.monotonic()
    kwargs: dict = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                    "stdin": subprocess.DEVNULL}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **kwargs)

    lock = threading.Lock()
    totals = [0, 0]
    buffers = [bytearray(), bytearray()]
    exceeded = [False]

    def reader(stream, index: int, cap: int) -> None:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            with lock:
                totals[index] += len(chunk)
                room = cap - len(buffers[index])
                if room > 0:
                    buffers[index].extend(chunk[:room])
                overall = totals[0] + totals[1]
            if overall > total_cap and not exceeded[0]:
                exceeded[0] = True
                _kill_process(proc)

    threads = [
        threading.Thread(target=reader, args=(proc.stdout, 0, stdout_cap), daemon=True),
        threading.Thread(target=reader, args=(proc.stderr, 1, stderr_cap), daemon=True),
    ]
    for thread in threads:
        thread.start()

    timed_out = False
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process(proc)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:  # pragma: no cover
            pass
    for thread in threads:
        thread.join(timeout=5)
    for stream in (proc.stdout, proc.stderr):
        try:
            stream.close()
        except (AttributeError, OSError):  # pragma: no cover
            pass

    duration = int((time.monotonic() - started) * 1000)
    stdout_truncated = totals[0] > stdout_cap
    stderr_truncated = totals[1] > stderr_cap
    any_truncated = stdout_truncated or stderr_truncated
    reason = "output_limit" if (exceeded[0] or any_truncated) else ("wall_clock" if timed_out else None)
    return BoundedResult(
        stdout=bytes(buffers[0]),
        stderr=bytes(buffers[1]),
        exit_code=proc.returncode if proc.returncode is not None else 137,
        duration_ms=duration,
        timed_out=timed_out,
        stdout_truncated=stdout_truncated or exceeded[0],
        stderr_truncated=stderr_truncated or exceeded[0],
        killed_reason=reason,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def collect_host_snapshot() -> dict:
    """采集 docker 所在宿主机的指纹（只读 /proc + statvfs，不执行 shell）。

    用于 UI 的「宿主完好」面板：任务前后各采一次，比较文件系统 inode、
    /etc 文件数、uptime、进程数、连接数是否漂移。

    注意：在容器内运行（例如 sandbox VM 上的 controller 容器）时，读到的是
    该容器的命名空间，而不是物理机；真实测试请在宿主机上直接跑
    `deploy/evidence/host_fingerprint.sh`。Windows 宿主无 /proc，返回 unavailable。
    """
    snapshot: dict = {"source": "host", "captured_at": _now(), "df_inodes": None,
                      "etc_files": None, "uptime": None, "proc_count": None,
                      "connections": None}
    if os.name == "nt" or not Path("/proc").exists():
        snapshot["source"] = "unavailable"
        return snapshot
    try:
        vfs = os.statvfs("/")
        used_pct = 0 if vfs.f_files == 0 else int(100 * (vfs.f_files - vfs.f_ffree) / vfs.f_files)
        snapshot["df_inodes"] = (
            f"total={vfs.f_files} free={vfs.f_ffree} used%={used_pct}"
        )
        etc_count = 0
        for _root, _dirs, files in os.walk("/etc"):
            etc_count += len(files)
            if etc_count > 100000:
                break
        snapshot["etc_files"] = etc_count
        uptime_s = float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
        snapshot["uptime"] = f"up {uptime_s:.0f}s"
        snapshot["proc_count"] = sum(
            1 for entry in Path("/proc").iterdir() if entry.name.isdigit()
        )
        conn = 0
        for name in ("/proc/net/tcp", "/proc/net/tcp6"):
            path = Path(name)
            if path.exists():
                conn += max(0, len(path.read_text(encoding="utf-8").splitlines()) - 1)
        snapshot["connections"] = conn
    except OSError as exc:  # 权限/文件缺失：如实降级，不伪造数据
        snapshot["source"] = "error"
        snapshot["error"] = f"{type(exc).__name__}: {exc}"
    return snapshot


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

    # --------------------------------------------------------------- readiness
    def _safe_run(self, args: list[str], timeout: int = 20) -> tuple[int, str, str]:
        """不抛异常的 docker 调用，用于健康检查/探测。"""
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 127, "", f"{type(exc).__name__}: {exc}"

    def daemon_status(self) -> dict:
        """返回 daemon 是否可达、版本、可用 runtime。不依赖 docker SDK。"""
        code, out, err = self._safe_run([self.docker_bin, "info", "--format",
                                         "{{.ServerVersion}}|{{json .Runtimes}}"])
        if code != 0:
            return {"ok": False, "error": err[:400] or f"docker exited {code}",
                    "version": None, "runtimes": []}
        version, _, runtimes_raw = out.partition("|")
        runtimes: list[str] = []
        try:
            runtimes = sorted(json.loads(runtimes_raw or "{}").keys())
        except json.JSONDecodeError:
            runtimes = []
        return {"ok": True, "error": None, "version": version, "runtimes": runtimes}

    def image_status(self, image: str | None = None) -> dict:
        target = image or self.image
        code, out, err = self._safe_run([self.docker_bin, "image", "inspect",
                                         "--format", "{{.Id}}", target])
        return {"ok": code == 0, "image": target,
                "id": out[:64] if code == 0 else None,
                "error": None if code == 0 else err[:300]}

    def ensure_ready(self) -> dict:
        """controller 启动/建容器前的前置检查：daemon、runtime、镜像。

        失败必须显式抛错，绝不静默降级 runtime（P1-6 要求）。
        """
        daemon = self.daemon_status()
        if not daemon["ok"]:
            raise DockerError(f"docker daemon unreachable: {daemon['error']}")
        runtime = self.runtime or "runc"
        if runtime not in daemon["runtimes"]:
            raise DockerError(
                f"runtime {runtime!r} not available; daemon runtimes={daemon['runtimes']}"
            )
        image = self.image_status()
        if not image["ok"]:
            raise DockerError(f"sandbox image missing: {image['error']}")
        return {"daemon": daemon, "runtime": runtime, "image": image}

    # ------------------------------------------------------------- lifecycle
    def create(self, task_id: str, limits: Limits) -> None:
        self._limits = limits
        self.ensure_ready()
        args = build_run_args(task_id, limits, image=self.image, runtime=self.runtime, network=self.network)
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            raise DockerError(f"docker run failed: {result.stderr.strip()[:400]}")
        self._container_id = result.stdout.strip()
        self._image_id = self.image_status().get("id") or ""

    def sandbox_info(self) -> dict:
        return {
            "backend": "docker",
            "runtime": self.runtime or "runc",
            "container_id": getattr(self, "_container_id", None),
            "image_id": getattr(self, "_image_id", None),
        }

    def destroy(self, task_id: str) -> dict:
        """删除容器并用 inspect 确认；只有确认不存在才返回 verified=True。"""
        name = f"yaig-{task_id}"
        rm = subprocess.run([self.docker_bin, "rm", "-f", name],
                            capture_output=True, text=True, timeout=30)
        inspect = subprocess.run([self.docker_bin, "inspect", "--format", "{{.Id}}", name],
                                 capture_output=True, text=True, timeout=20)
        exists = inspect.returncode == 0
        stderr = inspect.stderr.strip()
        absent = (not exists) and ("no such" in stderr.lower() or "not found" in stderr.lower())
        verified = absent and not exists
        return {
            "ok": verified,
            "verified": verified,
            "backend": "docker",
            "container": name,
            "rm_exit": rm.returncode,
            "inspect_exit": inspect.returncode,
            "error": None if verified else (stderr[:400] or "container may still exist"),
        }

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
        args = [self.docker_bin, "exec", "-w", cwd, f"yaig-{task_id}", "sh", "-lc", command]
        result = run_bounded(
            args,
            timeout_s=limit,
            stdout_cap=self._limits.max_output_bytes,
            stderr_cap=self._limits.max_output_bytes,
            total_cap=self._limits.max_total_output_bytes,
        )
        if result.timed_out or result.killed_reason == "output_limit":
            # 杀掉容器，确保 docker exec 的远端进程不会留在里面继续消耗资源。
            subprocess.run([self.docker_bin, "kill", f"yaig-{task_id}"],
                           capture_output=True, timeout=20)

        stdout = result.stdout.decode("utf-8", "replace")
        stderr = result.stderr.decode("utf-8", "replace")
        if result.stdout_truncated:
            stdout += "\n...[truncated]"
        if result.stderr_truncated:
            stderr += "\n...[truncated]"
        return ExecResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            timed_out=result.timed_out,
            truncated=result.truncated,
            killed_reason=result.killed_reason,
        )

    def host_fingerprint(self) -> dict:
        try:
            kernel = Path("/proc/version").read_text(encoding="utf-8", errors="replace")[:120]
        except OSError:
            kernel = "unknown"
        return {"kernel": kernel}

    def host_snapshot(self) -> dict:
        return collect_host_snapshot()

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
