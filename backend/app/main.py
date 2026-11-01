"""YAI Guard Web/API 控制面（部署在 yai-app VM 上，不持有任何执行权限）。

端点：
- GET  /                         任务页（静态 HTML）
- GET  /healthz                  健康检查
- GET  /api/policy               当前策略摘要（给 UI 展示）
- POST /api/tasks                提交任务
- GET  /api/tasks/{id}           任务状态
- GET  /api/tasks/{id}/events    SSE 事件流
- POST /api/tasks/{id}/approvals/{call_id}   人工审批
- GET  /api/tasks/{id}/trace.jsonl           审计导出

本地离线运行：
    MOCK_MODEL=1 SANDBOX_DRIVER=fake uvicorn app.main:app --reload
真实运行：
    SANDBOX_DRIVER=remote SANDBOX_BASE_URL=http://<sandbox-private-ip>:9000 \
    SANDBOX_TOKEN=... VULTR_INFERENCE_API_KEY=... uvicorn app.main:app
"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from fastapi import FastAPI, Header, HTTPException, Request
    from fastapi.responses import FileResponse, StreamingResponse
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("fastapi is required to run the API") from exc

from app.agent.loop import run_agent
from app.agent.mock_model import MockModel
from app.policy import Decision, EvalContext, PolicyEngine, ToolCall
from app.sandbox.fake_backend import FakeSandbox
from app.trace import TraceStore, new_trace_id, verify_file

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


def _pick_web() -> Path:
    env = os.environ.get("WEB_DIR")
    candidates = ([Path(env)] if env else []) + [REPO_ROOT / "web", BACKEND_ROOT / "web"]
    return next((p for p in candidates if (p / "index.html").exists()), REPO_ROOT / "web")


ARTIFACTS = Path(os.environ.get("ARTIFACT_DIR", str(REPO_ROOT / "artifacts")))
TRACES = ARTIFACTS / "traces"
WEB = _pick_web()

app = FastAPI(title="YAI Guard", version="0.1.0")
POLICY = PolicyEngine.from_file()
_LOCK = threading.Lock()


@dataclass
class TaskState:
    task_id: str
    trace_id: str
    task: str
    status: str = "queued"
    summary: str = ""
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    events: list[dict] = field(default_factory=list)


TASKS: dict[str, TaskState] = {}
PENDING: dict[tuple[str, str], dict[str, Any]] = {}

# 公网 Demo 保护：全局并发 = 1（演示期串行，保证容器生命周期可控）
_TASK_SLOT = threading.BoundedSemaphore(1)
_RATE_LOCK = threading.Lock()
_RATE_HITS: dict[str, list[float]] = {}
RATE_WINDOW_S = 60.0


def _require_demo_token(token: str | None) -> None:
    """DEMO_TOKEN 未设置时不鉴权（本地开发）；设置了就必须带 X-Demo-Token。"""
    expected = os.environ.get("DEMO_TOKEN", "")
    if expected and not secrets.compare_digest(token or "", expected):
        raise HTTPException(status_code=401, detail="missing or invalid X-Demo-Token")


def _rate_limit_per_min() -> int:
    try:
        return int(os.environ.get("RATE_LIMIT_PER_MIN", "10"))
    except ValueError:
        return 10


def _enforce_rate_limit(request: Request) -> None:
    """每 IP 令牌桶（滑动窗口）。<=0 表示关闭，仅内部/测试使用。"""
    limit = _rate_limit_per_min()
    if limit <= 0:
        return
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _RATE_LOCK:
        hits = [t for t in _RATE_HITS.get(ip, []) if now - t < RATE_WINDOW_S]
        if len(hits) >= limit:
            _RATE_HITS[ip] = hits
            raise HTTPException(
                status_code=429,
                detail=f"rate limit exceeded ({limit} req/min)",
                headers={"Retry-After": str(max(1, int(RATE_WINDOW_S - (now - hits[0]))))},
            )
        hits.append(now)
        _RATE_HITS[ip] = hits


# --------------------------------------------------------------------- runtime
def _build_sandbox():
    driver = os.environ.get("SANDBOX_DRIVER", "fake")
    if driver == "fake":
        return FakeSandbox()
    if driver == "remote":
        from app.sandbox.remote_backend import RemoteSandbox

        return RemoteSandbox(
            os.environ.get("SANDBOX_BASE_URL", "http://127.0.0.1:9000"),
            os.environ.get("SANDBOX_TOKEN", ""),
        )
    if driver == "docker":
        from app.sandbox.docker_backend import DockerSandbox

        return DockerSandbox(runtime=os.environ.get("SANDBOX_RUNTIME") or None)
    raise RuntimeError(f"unknown SANDBOX_DRIVER: {driver}")


def _build_model():
    if os.environ.get("MOCK_MODEL", "0") == "1" or not os.environ.get("VULTR_INFERENCE_API_KEY"):
        return MockModel()
    from app.agent.model_adapter import VultrModel

    model = VultrModel()
    model.assert_model()
    return model


# ------------------------------------------------------------------- approvals
def _approver(task_id: str):
    def approve(decision: Decision, call: ToolCall) -> bool:
        key = (task_id, call.id)
        gate = threading.Event()
        with _LOCK:
            PENDING[key] = {"decision": decision.to_dict(), "call": {"name": call.name, "arguments": call.arguments},
                            "event": gate, "approved": False}
        gate.wait(timeout=POLICY.approval_ttl)
        with _LOCK:
            record = PENDING.pop(key, {"approved": False})
        return bool(record.get("approved"))

    return approve


# ---------------------------------------------------------------- task runner
def _run_task(state: TaskState) -> None:
    trace = TraceStore(TRACES / f"{state.trace_id}.jsonl", state.trace_id)
    sandbox = None
    try:
        trace.append(task_id=state.task_id, actor="user", event="task.created",
                     payload={"task": state.task})
        state.events.append({"event": "task.created", "payload": {"task": state.task}})
        state.status = "running"

        sandbox = _build_sandbox()
        result = run_agent(
            task=state.task,
            model=_build_model(),
            policy=POLICY,
            sandbox=sandbox,
            trace=trace,
            task_id=state.task_id,
            approver=_approver(state.task_id),
            emit=state.events.append,
        )
        state.status = result.status
        state.summary = result.summary
        state.error = result.error
    except Exception as exc:  # noqa: BLE001 - 任何失败都要落到任务状态与审计
        state.status = "failed"
        state.error = f"{type(exc).__name__}: {exc}"
        try:
            trace.append(task_id=state.task_id, actor="system", event="task.failed",
                         payload={"error": state.error})
        except Exception:  # noqa: BLE001
            pass
    finally:
        if sandbox is not None:
            try:
                sandbox.destroy(state.task_id)
            except Exception:  # noqa: BLE001
                pass
        try:
            _TASK_SLOT.release()
        except ValueError:  # pragma: no cover - 只有异常路径可能重复释放
            pass


# ---------------------------------------------------------------------- routes
@app.get("/healthz")
def healthz() -> dict[str, Any]:
    ok, bad = True, None
    return {"ok": ok, "version": "0.1.0", "trace_ok": ok, "trace_bad": bad}


@app.get("/api/policy")
def get_policy() -> dict[str, Any]:
    return POLICY.summary()


@app.post("/api/tasks")
def create_task(body: dict[str, Any], request: Request,
                x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    _require_demo_token(x_demo_token)
    task = str(body.get("task", "")).strip()
    if not task:
        raise HTTPException(status_code=400, detail="task is required")
    _enforce_rate_limit(request)

    # 演示期全局串行：第二个任务直接 429，而不是排队；避免容器并发撑爆沙箱 VM。
    if not _TASK_SLOT.acquire(blocking=False):
        raise HTTPException(status_code=429,
                            detail="another task is already running (global concurrency = 1)",
                            headers={"Retry-After": "10"})

    task_id = "tk_" + uuid.uuid4().hex[:10]
    trace_id = new_trace_id()
    state = TaskState(task_id=task_id, trace_id=trace_id, task=task)
    with _LOCK:
        TASKS[task_id] = state
    try:
        threading.Thread(target=_run_task, args=(state,), daemon=True).start()
    except Exception:
        _TASK_SLOT.release()
        raise
    return {"task_id": task_id, "trace_id": trace_id, "status": state.status}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    state = TASKS.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="task not found")
    return {
        "task_id": state.task_id,
        "trace_id": state.trace_id,
        "status": state.status,
        "summary": state.summary,
        "error": state.error,
        "events": len(state.events),
    }


@app.get("/api/tasks/{task_id}/events")
def stream_events(task_id: str) -> StreamingResponse:
    state = TASKS.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="task not found")

    def generate():
        cursor = 0
        last_ping = time.monotonic()
        while True:
            with _LOCK:
                batch = state.events[cursor:]
                cursor += len(batch)
                finished = state.status in ("completed", "failed")
            for event in batch:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if finished and not batch:
                yield "event: done\ndata: {}\n\n"
                return
            # 心跳：防止 Caddy/浏览器把空闲连接判死（SSE 重连会丢开区间的事件）
            if time.monotonic() - last_ping >= 15.0:
                last_ping = time.monotonic()
                yield ": ping\n\n"
            time.sleep(0.2)

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/tasks/{task_id}/approvals/{call_id}")
def resolve_approval(task_id: str, call_id: str, body: dict[str, Any],
                     x_demo_token: str | None = Header(default=None)) -> dict[str, Any]:
    _require_demo_token(x_demo_token)
    key = (task_id, call_id)
    with _LOCK:
        record = PENDING.get(key)
        if not record:
            raise HTTPException(status_code=404, detail="approval not found or expired")
        record["approved"] = bool(body.get("approved", False))
        record["event"].set()
    return {"ok": True, "approved": record["approved"]}


@app.get("/api/tasks/{task_id}/trace/verify")
def verify_trace(task_id: str) -> dict[str, Any]:
    """对已落盘的 trace 重新计算哈希链：返回第一个坏事件的 seq。"""
    state = TASKS.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="task not found")
    path = TRACES / f"{state.trace_id}.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="trace not ready")
    ok, bad = verify_file(path)
    return {"ok": ok, "bad_seq": bad, "events": len(path.read_text(encoding="utf-8").splitlines())}


@app.get("/api/tasks/{task_id}/trace.jsonl")
def download_trace(task_id: str) -> FileResponse:
    state = TASKS.get(task_id)
    if not state:
        raise HTTPException(status_code=404, detail="task not found")
    path = TRACES / f"{state.trace_id}.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="trace not ready")
    return FileResponse(path, media_type="application/x-ndjson",
                        filename=f"{task_id}-trace.jsonl")


@app.get("/")
def index() -> FileResponse:
    page = WEB / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="web/index.html missing")
    return FileResponse(page)


@app.get("/styles.css")
def styles() -> FileResponse:
    path = WEB / "styles.css"
    if not path.exists():
        raise HTTPException(status_code=404, detail="web/styles.css missing")
    return FileResponse(path, media_type="text/css")


@app.get("/app.js")
def app_js() -> FileResponse:
    path = WEB / "app.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="web/app.js missing")
    return FileResponse(path, media_type="application/javascript")
