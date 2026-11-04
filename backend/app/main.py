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
from concurrent.futures import ThreadPoolExecutor
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
from app.tasks import ApprovalStatus, TaskService, TaskStatus
from app.tasks.reaper import reap_orphans as reap_task_orphans
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
TASK_STORE_MODE = os.environ.get("TASK_STORE", "memory").lower()
TASK_SERVICE = (
    TaskService.from_path(ARTIFACTS / "tasks.db")
    if TASK_STORE_MODE == "sqlite"
    else TaskService()
)


def _load_trace_signing_key():
    path = os.environ.get("TRACE_ED25519_PRIVATE_KEY_FILE")
    if not path:
        return None
    try:
        from app.trace.signing import load_private_key

        return load_private_key(path)
    except Exception:  # noqa: BLE001 - unsigned mode remains available
        return None


TRACE_ED25519_KEY = _load_trace_signing_key()
TRACE_ED25519_PUBLIC = (
    TRACE_ED25519_KEY.public_key() if TRACE_ED25519_KEY is not None else None
)

app = FastAPI(title="YAI Guard", version="0.1.0")
POLICY = PolicyEngine.from_file()
_LOCK = threading.Lock()

# Durable task runtime is single-writer by design. An advisory file lock makes
# a second uvicorn worker/process fail closed instead of double-executing tasks.
_RUNTIME_LOCK_HANDLE = None
_RUNTIME_LOCK_OK = True


def _acquire_runtime_lock() -> bool:
    global _RUNTIME_LOCK_HANDLE
    if not TASK_SERVICE.enabled:
        return True
    try:
        import fcntl
    except ImportError:  # non-Unix local development; CI/runtime is Linux
        return True
    try:
        handle = open(ARTIFACTS / "task-runtime.lock", "a+", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _RUNTIME_LOCK_HANDLE = handle
        return True
    except OSError:
        return False


_RUNTIME_LOCK_OK = _acquire_runtime_lock()


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
    cancel_event: threading.Event = field(default_factory=threading.Event)
    deadline_at: float = 0.0


TASKS: dict[str, TaskState] = {}
PENDING: dict[tuple[str, str], dict[str, Any]] = {}

# 执行槽与排队：审批等待会释放执行槽，避免一个审批卡死全部任务。
TASK_CONCURRENCY = max(1, int(os.environ.get("TASK_CONCURRENCY", "1")))
TASK_WAITING_THREADS = max(0, int(os.environ.get("TASK_WAITING_THREADS", "4")))
TASK_QUEUE_MAX = max(TASK_CONCURRENCY, int(os.environ.get("TASK_QUEUE_MAX", "8")))
_EXEC_SLOTS = threading.BoundedSemaphore(TASK_CONCURRENCY)
_TASK_EXECUTOR = ThreadPoolExecutor(
    max_workers=TASK_CONCURRENCY + TASK_WAITING_THREADS,
    thread_name_prefix="yaig-task",
)
_QUEUE_LOCK = threading.Lock()
_QUEUED = 0
_SSE_SLOTS = threading.BoundedSemaphore(int(os.environ.get("SSE_MAX_CONNECTIONS", "8")))
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


def _configured_admin_tokens() -> set[str]:
    raw = os.environ.get("ADMIN_TOKENS") or os.environ.get("ADMIN_TOKEN") or ""
    return {token.strip() for token in raw.split(",") if token.strip()}


def _require_admin(x_demo_token: str | None = Header(default=None)) -> str:
    """Destructive maintenance routes require an explicit admin token."""
    admins = _configured_admin_tokens()
    if not admins:
        raise HTTPException(status_code=503, detail="admin actions are not configured")
    if x_demo_token:
        for token in admins:
            if secrets.compare_digest(x_demo_token, token):
                return "admin"
    raise HTTPException(status_code=403, detail="admin token required")


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
            if state.status in ("completed", "failed", "cancelled", "interrupted")
            and now - state.created_at > TASK_STATE_TTL_S
        ]
        for task_id in stale:
            TASKS.pop(task_id, None)


def _owned_task(task_id: str, principal: str) -> TaskState:
    """任务不存在与无权访问都返回 404，避免通过状态码枚举别人的任务。"""
    state = TASKS.get(task_id)
    if state is None and TASK_SERVICE.enabled:
        record = TASK_SERVICE.get_record(task_id)
        if record is not None and record.owner == principal:
            state = TaskState(
                task_id=record.id,
                trace_id=record.trace_id,
                task=record.prompt,
                owner=record.owner,
                status=record.status.value,
                summary=record.summary,
                error=record.error,
            )
            with _LOCK:
                TASKS.setdefault(task_id, state)
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
def _release_exec_slot() -> None:
    try:
        _EXEC_SLOTS.release()
    except ValueError:  # pragma: no cover - defensive double release
        pass


def _acquire_exec_slot() -> None:
    _EXEC_SLOTS.acquire()


def _reserve_queue_slot() -> bool:
    global _QUEUED
    with _QUEUE_LOCK:
        if _QUEUED >= TASK_QUEUE_MAX:
            return False
        _QUEUED += 1
        return True


def _release_queue_slot() -> None:
    global _QUEUED
    with _QUEUE_LOCK:
        _QUEUED = max(0, _QUEUED - 1)


def _approver(state: TaskState):
    task_id, owner = state.task_id, state.owner

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
        state.status = "waiting_approval"
        TASK_SERVICE.mark_waiting(task_id)
        TASK_SERVICE.create_approval(
            task_id=task_id, call_id=call.id, owner=owner,
            ttl_s=POLICY.approval_ttl,
        )

        # 审批等待不占用执行槽：把槽让给其它任务，决定后再抢回。
        _release_exec_slot()
        now = time.monotonic()
        deadline = now + POLICY.approval_ttl
        if state.deadline_at:
            deadline = min(deadline, state.deadline_at)
        decided = False
        cancelled = False
        try:
            while True:
                if gate.wait(timeout=0.2):
                    decided = True
                    break
                if state.cancel_event.is_set():
                    cancelled = True
                    break
                if time.monotonic() >= deadline:
                    break
        finally:
            _acquire_exec_slot()

        state.status = "running"
        TASK_SERVICE.mark_resumed(task_id)
        with _LOCK:
            record = PENDING.pop(key, {"approved": False})
        approved = record.get("approved") is True
        if cancelled:
            status = ApprovalStatus.CANCELLED
        elif not decided:
            status = ApprovalStatus.EXPIRED
        else:
            status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        TASK_SERVICE.resolve_approval(task_id, call.id, status)
        return approved

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
    deadline_timer: threading.Timer | None = None
    heartbeat_stop: threading.Event | None = None
    _release_queue_slot()
    _EXEC_SLOTS.acquire()
    if TASK_SERVICE.enabled:
        record = TASK_SERVICE.get_record(state.task_id)
        if record is None or record.status is not TaskStatus.QUEUED:
            with _DISPATCH_LOCK:
                _DISPATCHED_QUEUED.discard(state.task_id)
            _release_exec_slot()
            return
        started = TASK_SERVICE.mark_running(state.task_id, worker_id="app")
        if started is None:
            with _DISPATCH_LOCK:
                _DISPATCHED_QUEUED.discard(state.task_id)
            _release_exec_slot()
            return
    with _DISPATCH_LOCK:
        _DISPATCHED_QUEUED.discard(state.task_id)
    try:
        state.status = "running"
        heartbeat_stop = threading.Event()

        def _heartbeat() -> None:
            while not heartbeat_stop.wait(20):
                TASK_SERVICE.heartbeat(state.task_id, worker_id="app", lease_seconds=60)

        if TASK_SERVICE.enabled:
            threading.Thread(target=_heartbeat, name=f"heartbeat-{state.task_id}",
                             daemon=True).start()
        deadline_timer = None
        task_timeout_s = int(POLICY.limits.get("task_timeout_s", 900))
        state.deadline_at = time.monotonic() + task_timeout_s
        # Hard wake at the task deadline: approval waits and slow steps return
        # to the loop, which records a task_timeout failure.
        deadline_timer = threading.Timer(task_timeout_s, state.cancel_event.set)
        deadline_timer.daemon = True
        deadline_timer.start()
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
            approver=_approver(state),
            emit=state.events.append,
            require_claims=os.environ.get("MOCK_MODEL", "0") != "1",
            should_cancel=state.cancel_event.is_set,
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
        if heartbeat_stop is not None:
            heartbeat_stop.set()
        if deadline_timer is not None:
            deadline_timer.cancel()
        # Seal before publishing the terminal status so readers never see a
        # completed task whose anchor was still missing.
        if trace is not None:
            try:
                trace.seal(TRACES / f"{state.trace_id}.anchor.json",
                           hmac_key=os.environ.get("TRACE_HMAC_KEY") or None,
                           ed25519_private_key=TRACE_ED25519_KEY)
            except Exception as exc:  # noqa: BLE001 - surface in state, do not crash
                final_status = "failed"
                final_error = final_error or f"anchor_seal_failed: {type(exc).__name__}: {exc}"
        TASK_SERVICE.finish(
            state.task_id, TaskStatus(final_status),
            summary=final_summary, error=final_error,
        )
        state.status = final_status
        state.summary = final_summary
        state.error = final_error
        _release_exec_slot()


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


_REAPER_STARTED = False
_REQUEUE_STARTED = False
_DISPATCH_LOCK = threading.Lock()
_DISPATCHED_QUEUED: set[str] = set()


def _run_orphan_reaper(*, grace_s: float = 60.0, dry_run: bool = False) -> dict[str, Any]:
    sandbox = _build_sandbox()
    return reap_task_orphans(
        TASK_SERVICE, sandbox, grace_s=grace_s, dry_run=dry_run,
    )


def _reaper_loop() -> None:
    interval = max(30, int(os.environ.get("REAPER_INTERVAL_S", "300")))
    while True:
        time.sleep(interval)
        try:
            _run_orphan_reaper()
        except Exception:  # noqa: BLE001 - background reaper must not crash the app
            pass


def _record_interrupted_events() -> list[str]:
    """Make recovery visible in the trace, idempotently, for every interrupted task."""
    recorded: list[str] = []
    if not TASK_SERVICE.enabled:
        return recorded
    for record in TASK_SERVICE.interrupted_tasks():
        path = TRACES / f"{record.trace_id}.jsonl"
        if not path.exists():
            continue
        try:
            trace = TraceStore(path, record.trace_id)
            events = trace.read()
            if not events:
                continue
            terminal_events = [
                e for e in events
                if e.get("event") in ("task.completed", "task.failed", "task.cancelled")
            ]
            if terminal_events:
                if len(terminal_events) == 1:
                    # Crash window: the trace reached a terminal state but the
                    # DB update did not land. Reconcile DB to the trace instead
                    # of appending a second terminal event.
                    event = terminal_events[0]
                    payload = event.get("payload") or {}
                    target = {
                        "task.completed": TaskStatus.COMPLETED,
                        "task.failed": TaskStatus.FAILED,
                        "task.cancelled": TaskStatus.CANCELLED,
                    }[event["event"]]
                    TASK_SERVICE.reconcile_terminal(
                        record.id, target,
                        summary=str(payload.get("summary", "")),
                        error=payload.get("error") or payload.get("reason"),
                    )
                    recorded.append(record.id)
                continue
            if any(e.get("event") == "task.interrupted" for e in events):
                continue
            trace.append(
                task_id=record.id, actor="system", event="task.interrupted",
                payload={"reason": "app_restart", "recovered_from": "durable_task_store"},
            )
            trace.seal(
                TRACES / f"{record.trace_id}.anchor.json",
                hmac_key=os.environ.get("TRACE_HMAC_KEY") or None,
                ed25519_private_key=TRACE_ED25519_KEY,
            )
            recorded.append(record.id)
        except Exception:  # noqa: BLE001 - one bad trace must not break startup
            continue
    return recorded


def _requeue_queued_tasks() -> dict[str, Any]:
    """Re-dispatch queued tasks; dispatched-but-not-started ones are not duplicated."""
    summary: dict[str, Any] = {"requeued": [], "cancelled": [], "skipped": []}
    if not TASK_SERVICE.enabled:
        summary["skipped"].append("task store disabled")
        return summary
    if os.environ.get("REQUEUE_ON_STARTUP", "1") == "0":
        summary["skipped"].append("disabled_by_env")
        return summary
    for record in TASK_SERVICE.queued_tasks():
        with _DISPATCH_LOCK:
            already_dispatched = record.id in _DISPATCHED_QUEUED
        if already_dispatched:
            summary["skipped"].append({"task_id": record.id, "reason": "already_dispatched"})
            continue
        if record.cancel_requested:
            TASK_SERVICE.finish(
                record.id, TaskStatus.CANCELLED,
                summary="cancelled before start",
            )
            summary["cancelled"].append(record.id)
            continue
        state = TaskState(
            task_id=record.id,
            trace_id=record.trace_id,
            task=record.prompt,
            owner=record.owner,
        )
        with _LOCK:
            TASKS.setdefault(record.id, state)
        if not _reserve_queue_slot():
            summary["skipped"].append({"task_id": record.id, "reason": "queue_full"})
            continue
        with _DISPATCH_LOCK:
            _DISPATCHED_QUEUED.add(record.id)
        try:
            _TASK_EXECUTOR.submit(_run_task, state)
        except Exception as exc:  # noqa: BLE001
            _release_queue_slot()
            with _DISPATCH_LOCK:
                _DISPATCHED_QUEUED.discard(record.id)
            TASKS.pop(record.id, None)
            summary["skipped"].append({
                "task_id": record.id,
                "reason": f"{type(exc).__name__}: {exc}",
            })
            continue
        summary["requeued"].append(record.id)
    return summary


def _requeue_loop() -> None:
    interval = max(10, int(os.environ.get("REQUEUE_INTERVAL_S", "30")))
    while True:
        time.sleep(interval)
        try:
            _requeue_queued_tasks()
        except Exception:  # noqa: BLE001 - background retry must not crash
            pass


@app.on_event("startup")
def _reap_stale_sandboxes() -> None:
    """Reap only containers that cannot belong to a healthy durable task."""
    global _REAPER_STARTED, _REQUEUE_STARTED
    if not _RUNTIME_LOCK_OK:
        return
    if os.environ.get("SANDBOX_DRIVER", "fake") != "remote":
        return
    if TASK_SERVICE.enabled:
        try:
            _run_orphan_reaper(grace_s=0)
        except Exception:  # noqa: BLE001 - startup cleanup is best-effort
            pass
        if os.environ.get("ORPHAN_REAPER", "1") != "0" and not _REAPER_STARTED:
            _REAPER_STARTED = True
            threading.Thread(target=_reaper_loop, name="orphan-reaper", daemon=True).start()
        try:
            _requeue_queued_tasks()
        except Exception:  # noqa: BLE001 - do not block startup
            pass
        if (os.environ.get("REQUEUE_ON_STARTUP", "1") != "0"
                and not _REQUEUE_STARTED):
            _REQUEUE_STARTED = True
            threading.Thread(target=_requeue_loop, name="queued-requeue",
                             daemon=True).start()
        try:
            _record_interrupted_events()
        except Exception:  # noqa: BLE001 - do not block startup
            pass
        return
    try:
        from app.sandbox.remote_backend import RemoteSandbox

        RemoteSandbox(
            os.environ.get("SANDBOX_BASE_URL", "http://127.0.0.1:9000"),
            os.environ.get("SANDBOX_TOKEN", ""),
            timeout_s=5.0,
        ).reap_orphans()
    except Exception:  # noqa: BLE001 - startup cleanup is best-effort
        pass


@app.post("/api/maintenance/reap")
def maintenance_reap(body: dict[str, Any] | None = None,
                     admin: str = Depends(_require_admin)) -> dict[str, Any]:
    """Admin-only SQLite-aware orphan reaper; dry_run defaults to false."""
    data = body or {}
    return _run_orphan_reaper(
        grace_s=float(data.get("grace_s", 60)),
        dry_run=bool(data.get("dry_run", False)),
    )


@app.get("/healthz")
def healthz() -> JSONResponse:
    sandbox = _sandbox_health()
    trace_ok, trace_error = _trace_health()
    model_ok = (
        os.environ.get("MOCK_MODEL", "0") == "1"
        or bool(os.environ.get("VULTR_INFERENCE_API_KEY"))
    )
    ok = bool(sandbox.get("ok")) and trace_ok and model_ok
    task_store = TASK_SERVICE.health()
    ok = ok and bool(task_store.get("ok")) and _RUNTIME_LOCK_OK
    payload = {
        "ok": ok,
        "version": "0.1.0",
        "trace_ok": trace_ok,
        "trace_bad": trace_error,
        "sandbox": sandbox,
        "model_configured": model_ok,
        "task_store": task_store,
        "runtime_lock": {"ok": _RUNTIME_LOCK_OK},
        "concurrency": {
            "execution_slots": TASK_CONCURRENCY,
            "waiting_threads": TASK_WAITING_THREADS,
            "queue_max": TASK_QUEUE_MAX,
            "queued": _QUEUED,
        },
        "signing": {
            "alg": "ed25519" if TRACE_ED25519_PUBLIC is not None else None,
            "configured": TRACE_ED25519_PUBLIC is not None,
        },
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
    if len(task) > int(os.environ.get("TASK_MAX_CHARS", "100000")):
        raise HTTPException(status_code=413, detail="task is too large")
    _enforce_rate_limit(request)
    _prune_tasks()
    if TASK_SERVICE.enabled and not _RUNTIME_LOCK_OK:
        raise HTTPException(status_code=503,
                            detail="another app instance owns the task runtime")

    # 有界队列：超过队列上限才拒绝；任务提交到 worker 池，状态先为 queued。
    if not _reserve_queue_slot():
        raise HTTPException(status_code=429,
                            detail="task queue is full",
                            headers={"Retry-After": "10"})

    task_id = "tk_" + uuid.uuid4().hex[:10]
    trace_id = new_trace_id()
    state = TaskState(task_id=task_id, trace_id=trace_id, task=task, owner=principal)
    with _LOCK:
        TASKS[task_id] = state
    TASK_SERVICE.create(
        task_id=task_id, trace_id=trace_id, owner=principal, prompt=task,
    )
    with _DISPATCH_LOCK:
        _DISPATCHED_QUEUED.add(task_id)
    try:
        _TASK_EXECUTOR.submit(_run_task, state)
    except Exception:
        with _DISPATCH_LOCK:
            _DISPATCHED_QUEUED.discard(task_id)
        _release_queue_slot()
        with _LOCK:
            TASKS.pop(task_id, None)
        raise
    return {"task_id": task_id, "trace_id": trace_id, "status": state.status}


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str,
                principal: str = Depends(_require_principal)) -> dict[str, Any]:
    """协作取消：设置标志、唤醒审批等待，由 worker 在边界写入 cancelled。"""
    state = _owned_task(task_id, principal)
    if state.status in ("completed", "failed", "cancelled", "interrupted"):
        raise HTTPException(status_code=409, detail="task is already terminal")
    state.cancel_event.set()
    TASK_SERVICE.request_cancel(task_id)
    return {"ok": True, "status": "cancelling"}


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
    if not _SSE_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="too many active SSE streams")

    def generate():
        cursor = 0
        last_ping = time.monotonic()
        try:
            while True:
                with _LOCK:
                    batch = state.events[cursor:]
                    cursor += len(batch)
                    finished = state.status in ("completed", "failed", "cancelled", "interrupted")
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
        finally:
            _SSE_SLOTS.release()

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
    TASK_SERVICE.resolve_approval(
        task_id, call_id,
        ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED,
    )
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
        public_key=TRACE_ED25519_PUBLIC,
        strict=state.status in ("completed", "failed", "cancelled", "interrupted"),
    )
    return result


@app.post("/api/containment/run")
def run_containment_lab(request: Request,
                        principal: str = Depends(_require_principal)) -> dict[str, Any]:
    """直接跑 5 个容器边界探针；不经过 policy/LLM。"""
    _enforce_rate_limit(request)
    if not _EXEC_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="all execution slots are busy")
    task_id = "ct_" + uuid.uuid4().hex[:10]
    try:
        sandbox = _build_sandbox()
        return run_containment(sandbox, task_id)
    finally:
        _release_exec_slot()


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

