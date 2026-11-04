"""HTTP routes for tasks, approvals, traces, evidence, health and UI."""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.containment import run_containment
from app import maintenance, security, settings, task_state, task_runner
from app.tasks import ApprovalStatus
from app.trace import new_trace_id, verify_trace_file
from app.trace.bundle import build_bundle

router = APIRouter()


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
        settings.TRACES.mkdir(parents=True, exist_ok=True)
        if not os.access(settings.TRACES, os.W_OK):
            return False, "trace directory is not writable"
        return True, None
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"


@router.post("/api/maintenance/reap")
def maintenance_reap(body: dict[str, Any] | None = None,
                     admin: str = Depends(security._require_admin)) -> dict[str, Any]:
    """Admin-only SQLite-aware orphan reaper; dry_run defaults to false."""
    data = body or {}
    return maintenance._run_orphan_reaper(
        grace_s=float(data.get("grace_s", 60)),
        dry_run=bool(data.get("dry_run", False)),
    )


@router.get("/healthz")
def healthz() -> JSONResponse:
    sandbox = _sandbox_health()
    trace_ok, trace_error = _trace_health()
    model_ok = (
        os.environ.get("MOCK_MODEL", "0") == "1"
        or bool(os.environ.get("VULTR_INFERENCE_API_KEY"))
    )
    ok = bool(sandbox.get("ok")) and trace_ok and model_ok
    task_store = settings.TASK_SERVICE.health()
    ok = ok and bool(task_store.get("ok")) and settings._RUNTIME_LOCK_OK
    payload = {
        "ok": ok,
        "version": settings.APP_VERSION,
        "trace_ok": trace_ok,
        "trace_bad": trace_error,
        "sandbox": sandbox,
        "model_configured": model_ok,
        "task_store": task_store,
        "runtime_lock": {"ok": settings._RUNTIME_LOCK_OK},
        "runtime_mode": settings.TASK_RUNTIME_MODE,
        "event_store": settings.TASK_EVENT_STORE,
        "approval_store": settings.TASK_APPROVAL_STORE,
        "concurrency": {
            "execution_slots": settings.TASK_WORKERS
            if settings.TASK_RUNTIME_MODE == "multi" else settings.TASK_CONCURRENCY,
            "waiting_threads": settings.TASK_WAITING_THREADS,
            "queue_max": settings.TASK_QUEUE_MAX,
            "queued": (settings.TASK_SERVICE.count_queued()
                       if settings.TASK_RUNTIME_MODE == "multi"
                       else task_state.queued_count()),
        },
        "signing": {
            "alg": "ed25519" if settings.TRACE_ED25519_PUBLIC is not None else None,
            "configured": settings.TRACE_ED25519_PUBLIC is not None,
        },
    }
    return JSONResponse(payload, status_code=200 if ok else 503)


@router.get("/api/policy")
def get_policy() -> dict[str, Any]:
    return settings.POLICY.summary()


@router.post("/api/tasks")
def create_task(body: dict[str, Any], request: Request,
                principal: str = Depends(security._require_principal)) -> dict[str, Any]:
    task = str(body.get("task", "")).strip()
    if not task:
        raise HTTPException(status_code=400, detail="task is required")
    if len(task) > int(os.environ.get("TASK_MAX_CHARS", "100000")):
        raise HTTPException(status_code=413, detail="task is too large")
    security._enforce_rate_limit(request)
    task_state._prune_tasks()
    if settings.TASK_RUNTIME_MODE == "multi" and not settings.TASK_SERVICE.enabled:
        raise HTTPException(status_code=503,
                            detail="multi-worker mode requires TASK_STORE=sqlite")
    if (settings.TASK_RUNTIME_MODE == "single" and settings.TASK_SERVICE.enabled
            and not settings._RUNTIME_LOCK_OK):
        raise HTTPException(status_code=503,
                            detail="another app instance owns the task runtime")

    task_id = "tk_" + uuid.uuid4().hex[:10]
    trace_id = new_trace_id()
    state = task_state.TaskState(task_id=task_id, trace_id=trace_id,
                                 task=task, owner=principal)
    if settings.TASK_RUNTIME_MODE == "multi":
        if settings.TASK_SERVICE.count_queued() >= settings.TASK_QUEUE_MAX:
            raise HTTPException(status_code=429, detail="task queue is full",
                                headers={"Retry-After": "10"})
        created = settings.TASK_SERVICE.create(
            task_id=task_id, trace_id=trace_id, owner=principal, prompt=task,
        )
        if settings.TASK_SERVICE.enabled and created is None:
            raise HTTPException(status_code=503,
                                detail="task store unavailable; task was not queued")
        with task_state._LOCK:
            task_state.TASKS[task_id] = state
        return {"task_id": task_id, "trace_id": trace_id, "status": "queued"}

    # 有界队列：超过队列上限才拒绝；任务提交到 worker 池，状态先为 queued。
    if not task_state._reserve_queue_slot():
        raise HTTPException(status_code=429,
                            detail="task queue is full",
                            headers={"Retry-After": "10"})

    created = settings.TASK_SERVICE.create(
        task_id=task_id, trace_id=trace_id, owner=principal, prompt=task,
    )
    if settings.TASK_SERVICE.enabled and created is None:
        task_state._release_queue_slot()
        raise HTTPException(status_code=503,
                            detail="task store unavailable; task was not queued")
    with task_state._LOCK:
        task_state.TASKS[task_id] = state
    with task_state._DISPATCH_LOCK:
        task_state._DISPATCHED_QUEUED.add(task_id)
    try:
        task_state._TASK_EXECUTOR.submit(task_runner.run_task, state)
    except Exception:
        with task_state._DISPATCH_LOCK:
            task_state._DISPATCHED_QUEUED.discard(task_id)
        task_state._release_queue_slot()
        with task_state._LOCK:
            task_state.TASKS.pop(task_id, None)
        raise
    return {"task_id": task_id, "trace_id": trace_id, "status": state.status}


@router.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str,
                principal: str = Depends(security._require_principal)) -> dict[str, Any]:
    """协作取消：设置标志、唤醒审批等待，由 worker 在边界写入 cancelled。"""
    state = task_state._owned_task(task_id, principal)
    if state.status in ("completed", "failed", "cancelled", "interrupted"):
        raise HTTPException(status_code=409, detail="task is already terminal")
    state.cancel_event.set()
    settings.TASK_SERVICE.request_cancel(task_id)
    return {"ok": True, "status": "cancelling"}


@router.get("/api/tasks/{task_id}")
def get_task(task_id: str,
             principal: str = Depends(security._require_principal)) -> dict[str, Any]:
    state = task_state._owned_task(task_id, principal)
    event_count = (settings.TASK_SERVICE.count_events(task_id)
                   if settings.TASK_EVENT_STORE == "db" else len(state.events))
    return {
        "task_id": state.task_id,
        "trace_id": state.trace_id,
        "status": state.status,
        "summary": state.summary,
        "error": state.error,
        "events": event_count,
    }


@router.get("/api/tasks/{task_id}/events")
def stream_events(task_id: str,
                  principal: str = Depends(security._require_principal)) -> StreamingResponse:
    state = task_state._owned_task(task_id, principal)
    sse_token: str | None = None
    if settings.TASK_RUNTIME_MODE == "multi" and settings.TASK_SERVICE.enabled:
        sse_token = uuid.uuid4().hex
        limit = int(os.environ.get("SSE_MAX_CONNECTIONS", "8"))
        if not settings.TASK_SERVICE.acquire_sse(sse_token, limit=limit, ttl_s=300):
            raise HTTPException(status_code=429, detail="too many active SSE streams")
    elif not task_state._SSE_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="too many active SSE streams")

    def generate():
        cursor = 0
        last_ping = time.monotonic()
        try:
            while True:
                if settings.TASK_EVENT_STORE == "db":
                    batch = settings.TASK_SERVICE.list_events(task_id, after_seq=cursor)
                    if batch:
                        cursor = max(int(event.get("seq", 0)) for event in batch)
                    record = settings.TASK_SERVICE.get_record(task_id)
                    if record is None:
                        # The task row was pruned (or the store is gone). Do
                        # not spin forever on an SSE connection that can never
                        # observe another terminal transition.
                        yield "event: done\ndata: {}\n\n"
                        return
                    finished = record.terminal
                else:
                    with task_state._LOCK:
                        batch = state.events[cursor:]
                        cursor += len(batch)
                        finished = state.status in ("completed", "failed",
                                                    "cancelled", "interrupted")
                for event in batch:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if finished and not batch:
                    yield "event: done\ndata: {}\n\n"
                    return
                # 心跳：防止 Caddy/浏览器把空闲连接判死（SSE 重连会丢开区间的事件）
                if time.monotonic() - last_ping >= 15.0:
                    last_ping = time.monotonic()
                    if sse_token is not None:
                        settings.TASK_SERVICE.renew_sse(sse_token)
                    yield ": ping\n\n"
                time.sleep(0.2)
        finally:
            if sse_token is not None:
                settings.TASK_SERVICE.release_sse(sse_token)
            else:
                task_state._SSE_SLOTS.release()

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/api/tasks/{task_id}/approvals/{call_id}")
def resolve_approval(task_id: str, call_id: str, body: dict[str, Any],
                     principal: str = Depends(security._require_principal)) -> dict[str, Any]:
    """严格布尔审批：字符串 "false"、数字 1、缺字段一律 422，绝不隐式转换。

    还校验：任务存在且属于当前 principal；call_id 对应该任务；不允许重复审批。
    """
    task_state._owned_task(task_id, principal)
    approved = body.get("approved", settings._MISSING)
    if approved is settings._MISSING or type(approved) is not bool:
        raise HTTPException(
            status_code=422,
            detail="'approved' must be a JSON boolean (true/false), not a string or number",
        )
    key = (task_id, call_id)
    if settings.TASK_APPROVAL_STORE == "db":
        record = settings.TASK_SERVICE.get_approval(task_id, call_id)
        if record is None or record.get("owner") != principal:
            raise HTTPException(status_code=404, detail="approval not found or expired")
        if record.get("status") != "pending":
            raise HTTPException(status_code=409, detail="approval already resolved")
        resolved = settings.TASK_SERVICE.resolve_approval(
            task_id, call_id,
            ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED,
        )
        if not resolved:
            raise HTTPException(status_code=409, detail="approval already resolved")
        return {"ok": True, "approved": approved}

    with task_state._LOCK:
        record = task_state.PENDING.get(key)
        if record is None:
            raise HTTPException(status_code=404, detail="approval not found or expired")
        if record.get("owner") != principal:
            raise HTTPException(status_code=404, detail="approval not found or expired")
        if record.get("resolved"):
            raise HTTPException(status_code=409, detail="approval already resolved")
        record["resolved"] = True
        record["approved"] = approved
        record["event"].set()
    settings.TASK_SERVICE.resolve_approval(
        task_id, call_id,
        ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED,
    )
    return {"ok": True, "approved": approved}


@router.get("/api/tasks/{task_id}/trace/verify")
def verify_trace(task_id: str,
                 principal: str = Depends(security._require_principal)) -> dict[str, Any]:
    """结构化校验：链完整性 + 序号连续 + 终止事件 + 锚点（+ 可选 HMAC）。"""
    state = task_state._owned_task(task_id, principal)
    path = settings.TRACES / f"{state.trace_id}.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="trace not ready")
    return verify_trace_file(
        path,
        task_status=state.status,
        anchor_path=settings.TRACES / f"{state.trace_id}.anchor.json",
        hmac_key=os.environ.get("TRACE_HMAC_KEY") or None,
        public_key=settings.TRACE_ED25519_PUBLIC,
        strict=state.status in ("completed", "failed", "cancelled", "interrupted"),
    )


@router.post("/api/containment/run")
def run_containment_lab(request: Request,
                        principal: str = Depends(security._require_principal)) -> dict[str, Any]:
    """直接跑 5 个容器边界探针；不经过 policy/LLM。"""
    security._enforce_rate_limit(request)
    if not task_state._EXEC_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="all execution slots are busy")
    task_id = "ct_" + uuid.uuid4().hex[:10]
    try:
        return run_containment(task_runner._build_sandbox(), task_id)
    finally:
        task_state._release_exec_slot()


@router.get("/api/tasks/{task_id}/evidence")
def download_evidence(task_id: str,
                      principal: str = Depends(security._require_principal)) -> dict[str, Any]:
    """可离线校验的 evidence bundle（内嵌 trace 事件 + anchor）。"""
    state = task_state._owned_task(task_id, principal)
    path = settings.TRACES / f"{state.trace_id}.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="trace not ready")
    return build_bundle(
        path,
        anchor_path=settings.TRACES / f"{state.trace_id}.anchor.json",
        hmac_key=os.environ.get("TRACE_HMAC_KEY") or None,
    )


@router.get("/api/tasks/{task_id}/trace.jsonl")
def download_trace(task_id: str,
                   principal: str = Depends(security._require_principal)) -> FileResponse:
    state = task_state._owned_task(task_id, principal)
    path = settings.TRACES / f"{state.trace_id}.jsonl"
    if not path.exists():
        raise HTTPException(status_code=404, detail="trace not ready")
    return FileResponse(path, media_type="application/x-ndjson",
                        filename=f"{task_id}-trace.jsonl")


@router.get("/")
def index() -> FileResponse:
    page = settings.WEB / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="web/index.html missing")
    return FileResponse(page)


@router.get("/styles.css")
def styles() -> FileResponse:
    path = settings.WEB / "styles.css"
    if not path.exists():
        raise HTTPException(status_code=404, detail="web/styles.css missing")
    return FileResponse(path, media_type="text/css")


@router.get("/app.js")
def app_js() -> FileResponse:
    path = settings.WEB / "app.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="web/app.js missing")
    return FileResponse(path, media_type="application/javascript")
