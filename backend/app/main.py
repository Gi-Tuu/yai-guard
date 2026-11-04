"""YAI Guard Web/API 控制面（部署在 yai-app VM 上，不持有任何执行权限）。

端点：
- GET  /                         任务页（静态 HTML）
- GET  /healthz                  健康检查
- GET  /api/policy               当前策略摘要（给 UI 展示）
- POST /api/tasks                create task
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

import hashlib
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
    from fastapi import Depends, FastAPI, Header, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("fastapi is required to run the API") from exc

from app.agent.loop import run_agent
from app.agent.mock_model import MockModel
from app.policy import Decision, EvalContext, PolicyEngine, ToolCall
from app.sandbox.fake_backend import FakeSandbox
from app.trace import TraceStore, new_trace_id, verify_trace_file
from app.trace.bundle import build_bundle
from app.containment import run_containment

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
    owner: str = "anonymous"
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
_MISSING = object()
TASK_STATE_TTL_S = int(os.environ.get("TASK_STATE_TTL_S", "3600"))


def _configured_tokens() -> set[str]:
    raw = os.environ.get("DEMO_TOKENS") or os.environ.get("DEMO_TOKEN") or ""
    return {token.strip() for token in raw.split(",") if token.strip()}


def _principal_id(token: str) -> str:
    """不把原始 token 存进任务状态/日志，只存稳定短 ID。"""
    return "u_" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _require_principal(x_demo_token: str | None = Header(default=None)) -> str:
    """统一鉴权依赖：除 /healthz、/api/policy 与静态资源外，全部任务接口都走它。

    生产模式（APP_ENV=production）没有配置 token 时在 import 阶段直接失败；
    开发/测试必须显式 ALLOW_ANONYMOUS=1 才能匿名，避免“忘记配 token 就公网裸奔”。
    """
    tokens = _configured_tokens()
    if not tokens:
        if os.environ.get("ALLOW_ANONYMOUS") == "1":
            return "anonymous"
        raise HTTPException(status_code=503, detail="server auth is not configured")
    if x_demo_token:
        for token in tokens:
            if secrets.compare_digest(x_demo_token, token):
                return _principal_id(token)
    raise HTTPException(status_code=401, detail="missing or invalid X-Demo-Token")


def _prune_tasks() -> None:
    """Drop old terminal task state so repeated demo runs do not grow forever.

    Trace/evidence files are intentionally retained; this only bounds the
    in-memory TASKS map.
    """
    now = time.time()
    with _LOCK:
        stale = [
            task_id
            for task_id, state in TASKS.items()
            if state.status in ("completed", "failed")
            and now - state.created_at > TASK_STATE_TTL_S
        ]
        for task_id in stale:
            TASKS.pop(task_id, None)


def _owned_task(task_id: str, principal: str) -> TaskState:
    """任务不存在与无权访问都返回 404，避免通过状态码枚举别人的任务。"""
    state = TASKS.get(task_id)
    if state is None or state.owner != principal:
        raise HTTPException(status_code=404, detail="task not found")
    return state


if os.environ.get("APP_ENV") == "production" and not _configured_tokens():
    raise RuntimeError("APP_ENV=production requires DEMO_TOKEN or DEMO_TOKENS")


def _rate_limit_per_min() -> int:
    try:
        return int(os.environ.get("RATE_LIMIT_PER_MIN", "10"))
    except ValueError:
        return 10


def _client_ip(request: Request) -> str:
    """Resolve the real client behind Caddy without trusting arbitrary headers.

    The app only listens on loopback and Caddy is the sole ingress, so the
    first X-Forwarded-For entry is the browser IP. Direct local calls without
    the header fall back to request.client.host.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        # Caddy appends the real peer to an existing X-Forwarded-For value, so
        # the last hop is the one we cannot spoof from the browser.
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def _enforce_rate_limit(request: Request) -> None:
    """每 IP 令牌桶（滑动窗口）。<=0 表示关闭，仅内部/测试使用。"""
    limit = _rate_limit_per_min()
    if limit <= 0:
        return
    ip = _client_ip(request)
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
def _approver(task_id: str, owner: str):
    def approve(decision: Decision, call: ToolCall) -> bool:
        key = (task_id, call.id)
        gate = threading.Event()
        with _LOCK:
            PENDING[key] = {
                "decision": decision.to_dict(),
                "call": {"name": call.name, "arguments": call.arguments},
                "event": gate,
                "approved": False,
                "resolved": False,
                "owner": owner,
            }
        gate.wait(timeout=POLICY.approval_ttl)
        with _LOCK:
            record = PENDING.pop(key, {"approved": False})
        return record.get("approved") is True

    return approve


# ---------------------------------------------------------------- task runner
def _run_task(state: TaskState) -> None:
    """Run one task while guaranteeing slot release and a terminal state.

    TraceStore construction itself can fail (disk full, unwritable artifact
    directory, corrupt trace). It must not leave the global demo slot acquired.
    """
    trace: TraceStore | None = None
    final_status = "failed"
    final_summary = ""
    final_error: str | None = None
    try:
        trace = TraceStore(TRACES / f"{state.trace_id}.jsonl", state.trace_id)
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
            approver=_approver(state.task_id, state.owner),
            emit=state.events.append,
            require_claims=os.environ.get("MOCK_MODEL", "0") != "1",
        )
        final_status = result.status
        final_summary = result.summary
        final_error = result.error
    except Exception as exc:  # noqa: BLE001 - any failure must become task state + trace
        final_status = "failed"
        final_error = f"{type(exc).__name__}: {exc}"
        if trace is not None:
            try:
                already = any(ev.get("event") == "task.failed" for ev in trace.read())
            except Exception:  # noqa: BLE001
                already = False
            if not already:
                try:
                    trace.append(task_id=state.task_id, actor="system", event="task.failed",
                                 payload={"error": final_error})
                except Exception:  # noqa: BLE001
                    pass
    finally:
        # Seal before publishing the terminal status so readers never see a
        # completed task whose anchor was still missing.
        if trace is not None:
            try:
                trace.seal(TRACES / f"{state.trace_id}.anchor.json",
                           hmac_key=os.environ.get("TRACE_HMAC_KEY") or None)
            except Exception as exc:  # noqa: BLE001 - surface in state, do not crash
                final_status = "failed"
                final_error = final_error or f"anchor_seal_failed: {type(exc).__name__}: {exc}"
        state.status = final_status
        state.summary = final_summary
        state.error = final_error
        try:
            _TASK_SLOT.release()
        except ValueError:  # pragma: no cover - only a defensive double release
            pass


# ---------------------------------------------------------------------- routes
def _sandbox_health() -> dict[str, Any]:
    driver = os.environ.get("SANDBOX_DRIVER", "fake")
    if driver == "fake":
        return {"ok": True, "backend": "fake"}
    if driver == "remote":
        from app.sandbox.remote_backend import RemoteSandbox

        payload = RemoteSandbox(
            os.environ.get("SANDBOX_BASE_URL", "http://127.0.0.1:9000"),
            os.environ.get("SANDBOX_TOKEN", ""),
            timeout_s=3.0,
        ).host_fingerprint()
        return {"ok": payload.get("ok") is True, "backend": "remote",
                "runtime": payload.get("runtime"), "error": payload.get("error")}
    if driver == "docker":
        from app.sandbox.docker_backend import DockerSandbox

        status = DockerSandbox(runtime=os.environ.get("SANDBOX_RUNTIME") or None).daemon_status()
        return {"ok": bool(status.get("ok")), "backend": "docker",
                "version": status.get("version"), "error": status.get("error")}
    return {"ok": False, "backend": driver, "error": "unknown sandbox driver"}


def _trace_health() -> tuple[bool, str | None]:
    try:
        TRACES.mkdir(parents=True, exist_ok=True)
        if not os.access(TRACES, os.W_OK):
            return False, "trace directory is not writable"
        return True, None
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


@app.get("/healthz")
def healthz() -> JSONResponse:
    sandbox = _sandbox_health()
    trace_ok, trace_error = _trace_health()
    model_ok = (
        os.environ.get("MOCK_MODEL", "0") == "1"
        or bool(os.environ.get("VULTR_INFERENCE_API_KEY"))
    )
    ok = bool(sandbox.get("ok")) and trace_ok and model_ok
    payload = {
        "ok": ok,
        "version": "0.1.0",
        "trace_ok": trace_ok,
        "trace_bad": trace_error,
        "sandbox": sandbox,
        "model_configured": model_ok,
    }
    return JSONResponse(payload, status_code=200 if ok else 503)


@app.get("/api/policy")
def get_policy() -> dict[str, Any]:
    return POLICY.summary()


@app.post("/api/tasks")
def create_task(body: dict[str, Any], request: Request,
                principal: str = Depends(_require_principal)) -> dict[str, Any]:
    task = str(body.get("task", "")).strip()
    if not task:
        raise HTTPException(status_code=400, detail="task is required")
    _enforce_rate_limit(request)
    _prune_tasks()

    # 演示期全局串行：第二个任务直接 429，而不是排队；避免容器并发撑爆沙箱 VM。
    if not _TASK_SLOT.acquire(blocking=False):
        raise HTTPException(status_code=429,
                            detail="another task is already running (global concurrency = 1)",
                            headers={"Retry-After": "10"})

    task_id = "tk_" + uuid.uuid4().hex[:10]
    trace_id = new_trace_id()
    state = TaskState(task_id=task_id, trace_id=trace_id, task=task, owner=principal)
    with _LOCK:
        TASKS[task_id] = state
    try:
        threading.Thread(target=_run_task, args=(state,), daemon=True).start()
    except Exception:
        _TASK_SLOT.release()
        raise
    return {"task_id": task_id, "trace_id": trace_id, "status": state.status}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, principal: str = Depends(_require_principal)) -> dict[str, Any]:
    state = _owned_task(task_id, principal)
    return {
        "task_id": state.task_id,
        "trace_id": state.trace_id,
        "status": state.status,
        "summary": state.summary,
        "error": state.error,
        "events": len(state.events),
    }


@app.get("/api/tasks/{task_id}/events")
def stream_events(task_id: str, principal: str = Depends(_require_principal)) -> StreamingResponse:
    state = _owned_task(task_id, principal)

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
                     principal: str = Depends(_require_principal)) -> dict[str, Any]:
    """严格布尔审批：字符串 "false"、数字 1、缺字段一律 422，绝不隐式转换。

    还校验：任务存在且属于当前 principal；call_id 对应该任务；不允许重复审批。
    """
    _owned_task(task_id, principal)
    approved = body.get("approved", _MISSING)
    if approved is _MISSING or type(approved) is not bool:
        raise HTTPException(
            status_code=422,
            detail="'approved' must be a JSON boolean (true/false), not a string or number",
        )
    key = (task_id, call_id)
    with _LOCK:
        record = PENDING.get(key)
        if record is None:
            raise HTTPException(status_code=404, detail="approval not found or expired")
        if record.get("owner") != principal:
            raise HTTPException(status_code=404, detail="approval not found or expired")
        if record.get("resolved"):
            raise HTTPException(status_code=409, detail="approval already resolved")
        record["resolved"] = True
        record["approved"] = approved
        record["event"].set()
    return {"ok": True, "approved": approved}


@app.get("/api/tasks/{task_id}/trace/verify")
def verify_trace(task_id: str, principal: str = Depends(_require_principal)) -> dict[str, Any]:
    """结构化校验：链完整性 + 序号连续 + 终止事件 + 锚点（+ 可选 HMAC）。"""
    state = _owned_task(task_id, principal)
    path = TRACES / f"{state.trace_id}.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="trace not ready")
    result = verify_trace_file(
        path,
        task_status=state.status,
        anchor_path=TRACES / f"{state.trace_id}.anchor.json",
        hmac_key=os.environ.get("TRACE_HMAC_KEY") or None,
        strict=state.status in ("completed", "failed"),
    )
    return result


@app.post("/api/containment/run")
def run_containment_lab(principal: str = Depends(_require_principal)) -> dict[str, Any]:
    """直接跑 4 个容器边界探针；不经过 policy/LLM。"""
    if not _TASK_SLOT.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="a task is already running")
    task_id = "ct_" + uuid.uuid4().hex[:10]
    try:
        sandbox = _build_sandbox()
        return run_containment(sandbox, task_id)
    finally:
        try:
            _TASK_SLOT.release()
        except ValueError:  # pragma: no cover
            pass


@app.get("/api/tasks/{task_id}/evidence")
def download_evidence(task_id: str, principal: str = Depends(_require_principal)) -> dict[str, Any]:
    """可离线校验的 evidence bundle（内嵌 trace 事件 + anchor）。"""
    state = _owned_task(task_id, principal)
    path = TRACES / f"{state.trace_id}.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="trace not ready")
    return build_bundle(
        path,
        anchor_path=TRACES / f"{state.trace_id}.anchor.json",
        hmac_key=os.environ.get("TRACE_HMAC_KEY") or None,
    )


@app.get("/api/tasks/{task_id}/trace.jsonl")
def download_trace(task_id: str, principal: str = Depends(_require_principal)) -> FileResponse:
    state = _owned_task(task_id, principal)
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

