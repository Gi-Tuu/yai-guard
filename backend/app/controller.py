"""Sandbox Controller：部署在 yai-sandbox VM 上，唯一持有 Docker 权限的 HTTP 服务。

安全约束（见 03/04/05 文档）：
- 只监听 VPC 私网地址（或 127.0.0.1），绝不暴露公网
- 所有请求必须带 X-Sandbox-Token（共享密钥，仅 app VM 持有）
- 不接受任意镜像/任意宿主路径：create 只接受 task_id，镜像由服务端固定
"""
from __future__ import annotations

import os
import threading
from typing import Any

try:
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import JSONResponse
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("fastapi is required to run the sandbox controller") from exc

from app.sandbox.docker_backend import DockerSandbox
from app.sandbox.interface import Limits

app = FastAPI(title="YAI Guard Sandbox Controller", version="0.1.0")

_TOKEN = os.environ.get("SANDBOX_TOKEN", "")
_IMAGE = os.environ.get("SANDBOX_IMAGE", "yaiguard/sandbox-py:1.0")
_RUNTIME = os.environ.get("SANDBOX_RUNTIME") or "runc"
_backend = DockerSandbox(image=_IMAGE, runtime=_RUNTIME)
_BACKEND_LOCK = threading.Lock()
_BACKENDS: dict[str, DockerSandbox] = {}

# 启动即检查 daemon / runtime / 镜像；失败时 /healthz 返回 503，且 create 会显式报错。
# 绝不静默把 runsc 降级成 runc（P1-6）。
try:
    _STARTUP: dict[str, Any] = {"ok": True, **_backend.ensure_ready()}
    # Do not clear all sandbox containers here: the app may still be running
    # and owning active tasks. The app-side reaper owns task-aware cleanup.
    _STARTUP["reaped"] = []
except Exception as exc:  # noqa: BLE001 - 启动失败要作为状态暴露，而不是 crash loop
    _STARTUP = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _auth(token: str | None) -> None:
    if not _TOKEN or token != _TOKEN:
        raise HTTPException(status_code=401, detail="invalid sandbox token")


@app.get("/healthz")
def healthz() -> JSONResponse:
    payload: dict[str, Any] = {
        "ok": bool(_STARTUP.get("ok")),
        "runtime": _RUNTIME,
        "image": _IMAGE,
        "daemon": _STARTUP.get("daemon"),
        "image_status": _STARTUP.get("image"),
        "error": _STARTUP.get("error"),
    }
    return JSONResponse(payload, status_code=200 if payload["ok"] else 503)


@app.get("/v1/host/snapshot")
def host_snapshot(x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    """宿主指纹：供 app VM 的「宿主完好」面板前后对比。

    不加 token 校验会泄露主机负载信息，因此和其它端点一致要求 token。
    """
    _auth(x_sandbox_token)
    return {"ok": True, "snapshot": _backend.host_snapshot()}


@app.delete("/v1/containers/{task_id}")
def remove_orphan_container(task_id: str,
                            x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    """Remove a labeled container even when no in-process backend owns it."""
    _auth(x_sandbox_token)
    result = _backend.remove_orphan(task_id)
    if not result.get("verified"):
        raise HTTPException(status_code=500, detail={
            "error": "orphan remove could not be verified", "result": result,
        })
    with _BACKEND_LOCK:
        _BACKENDS.pop(task_id, None)
    return {"ok": True, **result}


@app.get("/v1/containers")
def list_containers(x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_sandbox_token)
    return {"ok": True, "containers": _backend.list_containers()}


@app.post("/v1/orphans/reap")
def reap_orphans(x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    """Called by the app on startup; all app-side task state is gone by then."""
    _auth(x_sandbox_token)
    with _BACKEND_LOCK:
        removed = _backend.reap_orphans()
        _BACKENDS.clear()
    return {"ok": True, "removed": removed}


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
        max_total_output_bytes=int(data.get("max_total_output_bytes", 1_048_576)),
    )
    with _BACKEND_LOCK:
        if task_id in _BACKENDS:
            raise HTTPException(status_code=409, detail="task_id already exists")
        backend = DockerSandbox(image=_IMAGE, runtime=_RUNTIME)
        backend.create(task_id, parsed)
        _BACKENDS[task_id] = backend
        info = backend.sandbox_info()
    return {
        "ok": True, "task_id": task_id, "limits": parsed.to_dict(),
        "container_id": info.get("container_id"),
        "image_id": info.get("image_id"),
        "runtime": info.get("runtime"),
    }


@app.post("/v1/sandbox/{task_id}/exec")
def execute(task_id: str, body: dict[str, Any],
            x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_sandbox_token)
    with _BACKEND_LOCK:
        backend = _BACKENDS.get(task_id)
    if backend is None:
        raise HTTPException(status_code=404, detail="sandbox not found")
    result = backend.exec(
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
    with _BACKEND_LOCK:
        backend = _BACKENDS.get(task_id)
    if backend is None:
        raise HTTPException(status_code=404, detail="sandbox not found")
    backend.put_file(task_id, path, data)
    return {"ok": True, "bytes": len(data)}


@app.get("/v1/sandbox/{task_id}/file")
def get_file(task_id: str, path: str, max_bytes: int = 1_048_576,
             allow_missing: bool = True,
             x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_sandbox_token)
    import base64

    with _BACKEND_LOCK:
        backend = _BACKENDS.get(task_id)
    if backend is None:
        raise HTTPException(status_code=404, detail="sandbox not found")
    data = backend.get_file(task_id, path, max_bytes=max_bytes,
                            allow_missing=allow_missing)
    return {"ok": True, "base64": base64.b64encode(data).decode("ascii")}


@app.get("/v1/sandbox/{task_id}/files")
def list_files(task_id: str, path: str = "/workspace",
               x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_sandbox_token)
    with _BACKEND_LOCK:
        backend = _BACKENDS.get(task_id)
    if backend is None:
        raise HTTPException(status_code=404, detail="sandbox not found")
    entries = backend.list_files(task_id, path)
    return {"ok": True, "entries": entries}


@app.delete("/v1/sandbox/{task_id}")
def destroy(task_id: str,
            x_sandbox_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_sandbox_token)
    with _BACKEND_LOCK:
        backend = _BACKENDS.get(task_id)
    if backend is None:
        # A controller restart loses _BACKENDS but the container may still
        # exist. Remove by name and verify instead of claiming already_gone.
        result = _backend.remove_orphan(task_id)
        if not result.get("verified"):
            raise HTTPException(status_code=500, detail={
                "error": "container destroy could not be verified", "result": result,
            })
        with _BACKEND_LOCK:
            _BACKENDS.pop(task_id, None)
        return {"ok": True, **result}
    result = backend.destroy(task_id)
    if not result.get("verified"):
        raise HTTPException(status_code=500, detail={
            "error": "container destroy could not be verified", "result": result,
        })
    with _BACKEND_LOCK:
        _BACKENDS.pop(task_id, None)
    return {"ok": True, **result}
