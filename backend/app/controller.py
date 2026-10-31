"""Sandbox Controller：部署在 yai-sandbox VM 上，唯一持有 Docker 权限的 HTTP 服务。

安全约束（见 03/04/05 文档）：
- 只监听 VPC 私网地址（或 127.0.0.1），绝不暴露公网
- 所有请求必须带 X-Sandbox-Token（共享密钥，仅 app VM 持有）
- 不接受任意镜像/任意宿主路径：create 只接受 task_id，镜像由服务端固定
"""
from __future__ import annotations

import os
from typing import Any

try:
    from fastapi import FastAPI, Header, HTTPException, Request
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("fastapi is required to run the sandbox controller") from exc

from app.sandbox.docker_backend import DockerSandbox
from app.sandbox.interface import Limits

app = FastAPI(title="YAI Guard Sandbox Controller", version="0.1.0")

_TOKEN = os.environ.get("SANDBOX_TOKEN", "")
_IMAGE = os.environ.get("SANDBOX_IMAGE", "yaiguard/sandbox-py:1.0")
_RUNTIME = os.environ.get("SANDBOX_RUNTIME") or None
_backend = DockerSandbox(image=_IMAGE, runtime=_RUNTIME)


def _auth(token: str | None) -> None:
    if not _TOKEN or token != _TOKEN:
        raise HTTPException(status_code=401, detail="invalid sandbox token")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "runtime": _RUNTIME or "runc", "image": _IMAGE}


@app.post("/v1/sandbox/{task_id}")
def create(task_id: str, limits: dict[str, Any] | None = None,
           x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_sandbox_token)
    data = limits or {}
    parsed = Limits(
        cpu=float(data.get("cpus", data.get("cpu", 1.0))),
        memory_mb=int(data.get("memory_mb", 512)),
        pids=int(data.get("pids", 128)),
        nofile=int(data.get("nofile", 512)),
        timeout_s=int(data.get("timeout_s", 120)),
        max_output_bytes=int(data.get("max_output_bytes", 8192)),
    )
    _backend.create(task_id, parsed)
    return {"ok": True, "task_id": task_id, "limits": parsed.to_dict()}


@app.post("/v1/sandbox/{task_id}/exec")
def execute(task_id: str, body: dict[str, Any],
            x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_sandbox_token)
    result = _backend.exec(
        task_id,
        str(body.get("command", "")),
        timeout_s=int(body.get("timeout_s", 120)),
        cwd=str(body.get("cwd", "/workspace")),
    )
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "duration_ms": result.duration_ms,
        "timed_out": result.timed_out,
        "truncated": result.truncated,
        "killed_reason": result.killed_reason,
    }


@app.put("/v1/sandbox/{task_id}/file")
async def put_file(task_id: str, path: str, request: Request,
                   x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_sandbox_token)
    data = await request.body()
    _backend.put_file(task_id, path, data)
    return {"ok": True, "bytes": len(data)}


@app.get("/v1/sandbox/{task_id}/file")
def get_file(task_id: str, path: str,
             x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_sandbox_token)
    import base64

    data = _backend.get_file(task_id, path)
    return {"ok": True, "base64": base64.b64encode(data).decode("ascii")}


@app.get("/v1/sandbox/{task_id}/files")
def list_files(task_id: str, path: str = "/workspace",
               x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_sandbox_token)
    return {"ok": True, "entries": _backend.list_files(task_id, path)}


@app.delete("/v1/sandbox/{task_id}")
def destroy(task_id: str,
            x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_sandbox_token)
    _backend.destroy(task_id)
    return {"ok": True}
