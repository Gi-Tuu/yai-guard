"""Sandbox/model construction, approval waits and one task's execution."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from app.agent.loop import run_agent
from app.agent.mock_model import MockModel
from app.policy import Decision, ToolCall
from app.sandbox.fake_backend import FakeSandbox
from app.tasks import ApprovalStatus, TaskStatus
from app.trace import TraceStore
from app import settings
from app import task_state
from app import quiesce
from app.task_state import TaskState


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


def _settle_approval_status(*, decided: bool, cancelled: bool, approved: bool,
                            observed: str | None = None) -> ApprovalStatus:
    """集中映射审批结束状态，DB 和内存分支共用。"""
    if cancelled:
        return ApprovalStatus.CANCELLED
    if observed is not None:
        return ApprovalStatus(observed)
    if not decided:
        return ApprovalStatus.EXPIRED
    return ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED


def _wait_approval_db(state: TaskState, call: ToolCall,
                      deadline: float) -> tuple[bool, bool, bool, str | None]:
    approved = False
    decided = False
    cancelled = False
    observed: str | None = None
    try:
        while True:
            record = settings.TASK_SERVICE.get_approval(state.task_id, call.id)
            status = (record or {}).get("status")
            if status == "approved":
                approved, decided = True, True
                break
            if status in ("rejected", "expired", "cancelled"):
                observed = status
                decided = True
                break
            if state.cancel_event.is_set():
                cancelled = True
                break
            current = settings.TASK_SERVICE.get_record(state.task_id)
            if current is not None and current.cancel_requested:
                cancelled = True
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.2)
    finally:
        task_state._acquire_exec_slot()
    state.status = "running"
    settings.TASK_SERVICE.mark_resumed(
        state.task_id, lease_seconds=settings.TASK_LEASE_SECONDS,
    )
    return approved, decided, cancelled, observed


def _wait_approval_memory(state: TaskState, call: ToolCall, decision: Decision,
                          deadline: float) -> tuple[bool, bool, bool]:
    decided = False
    cancelled = False
    gate = threading.Event()
    key = (state.task_id, call.id)
    with task_state._LOCK:
        task_state.PENDING[key] = {
            "decision": decision.to_dict(),
            "call": {"name": call.name, "arguments": call.arguments},
            "event": gate,
            "approved": False,
            "resolved": False,
            "owner": state.owner,
        }
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
        task_state._acquire_exec_slot()
    state.status = "running"
    settings.TASK_SERVICE.mark_resumed(
        state.task_id, lease_seconds=settings.TASK_LEASE_SECONDS,
    )
    with task_state._LOCK:
        record = task_state.PENDING.pop(key, {"approved": False})
    return record.get("approved") is True, decided, cancelled


def build_approver(state: TaskState):
    """Return the approver closure; DB and memory waits stay separate."""
    def approve(decision: Decision, call: ToolCall) -> bool:
        task_id, owner = state.task_id, state.owner
        state.status = "waiting_approval"
        settings.TASK_SERVICE.mark_waiting(task_id)
        settings.TASK_SERVICE.create_approval(
            task_id=task_id, call_id=call.id, owner=owner,
            ttl_s=settings.POLICY.approval_ttl,
        )
        # 审批等待不占用执行槽：把槽让给其它任务，决定后再抢回。
        task_state._release_exec_slot()
        deadline = time.monotonic() + settings.POLICY.approval_ttl
        if state.deadline_at:
            deadline = min(deadline, state.deadline_at)

        if settings.TASK_APPROVAL_STORE == "db":
            approved, decided, cancelled, observed = _wait_approval_db(
                state, call, deadline,
            )
            settings.TASK_SERVICE.resolve_approval(
                task_id, call.id,
                _settle_approval_status(
                    decided=decided, cancelled=cancelled,
                    approved=approved, observed=observed,
                ),
            )
            return approved

        approved, decided, cancelled = _wait_approval_memory(
            state, call, decision, deadline,
        )
        settings.TASK_SERVICE.resolve_approval(
            task_id, call.id,
            _settle_approval_status(
                decided=decided, cancelled=cancelled, approved=approved,
            ),
        )
        return approved

    return approve


@dataclass
class RunContext:
    state: TaskState
    trace: TraceStore | None = None
    deadline_timer: threading.Timer | None = None
    heartbeat_stop: threading.Event | None = None
    final_status: str = "failed"
    final_summary: str = ""
    final_error: str | None = None


def _prepare_run(state: TaskState, *, already_claimed: bool,
                 slot_held: bool) -> RunContext | None:
    if quiesce.is_quiesced() and not already_claimed:
        # A queued task may have been marked dispatched by the route before the
        # marker appeared. Remove that marker, otherwise the post-quiesce
        # requeue loop will skip the task forever.
        with task_state._DISPATCH_LOCK:
            task_state._DISPATCHED_QUEUED.discard(state.task_id)
        task_state._release_queue_slot()
        return None
    if not already_claimed:
        task_state._release_queue_slot()
    if not slot_held:
        task_state._EXEC_SLOTS.acquire()
    if settings.TASK_SERVICE.enabled and not already_claimed:
        record = settings.TASK_SERVICE.get_record(state.task_id)
        if record is None or record.status is not TaskStatus.QUEUED:
            with task_state._DISPATCH_LOCK:
                task_state._DISPATCHED_QUEUED.discard(state.task_id)
            task_state._release_exec_slot()
            return None
        started = settings.TASK_SERVICE.mark_running(
            state.task_id, worker_id=state.worker_id,
            lease_seconds=settings.TASK_LEASE_SECONDS,
        )
        if started is None:
            with task_state._DISPATCH_LOCK:
                task_state._DISPATCHED_QUEUED.discard(state.task_id)
            task_state._release_exec_slot()
            return None
    with task_state._DISPATCH_LOCK:
        task_state._DISPATCHED_QUEUED.discard(state.task_id)
    return RunContext(state=state)


def _start_run_controls(ctx: RunContext) -> None:
    state = ctx.state
    state.status = "running"
    ctx.heartbeat_stop = threading.Event()

    def _heartbeat() -> None:
        while not ctx.heartbeat_stop.wait(settings.TASK_HEARTBEAT_SECONDS):
            settings.TASK_SERVICE.heartbeat(
                state.task_id, worker_id=state.worker_id,
                lease_seconds=settings.TASK_LEASE_SECONDS,
            )

    if settings.TASK_SERVICE.enabled:
        threading.Thread(target=_heartbeat, name=f"heartbeat-{state.task_id}",
                         daemon=True).start()
    task_timeout_s = int(settings.POLICY.limits.get("task_timeout_s", 900))
    state.deadline_at = time.monotonic() + task_timeout_s
    ctx.deadline_timer = threading.Timer(task_timeout_s, state.cancel_event.set)
    ctx.deadline_timer.daemon = True
    ctx.deadline_timer.start()


def _open_trace(ctx: RunContext) -> None:
    state = ctx.state
    ctx.trace = TraceStore(settings.TRACES / f"{state.trace_id}.jsonl",
                           state.trace_id)
    ctx.trace.append(task_id=state.task_id, actor="user", event="task.created",
                     payload={"task": state.task})
    state.events.append({"event": "task.created", "payload": {"task": state.task}})
    state.status = "running"


def _execute_task(ctx: RunContext) -> None:
    state = ctx.state
    assert ctx.trace is not None

    def _emit_event(event: dict[str, Any]) -> None:
        state.events.append(event)
        if settings.TASK_SERVICE.enabled:
            settings.TASK_SERVICE.append_event(state.task_id, event)

    def _should_cancel() -> bool:
        if state.cancel_event.is_set():
            return True
        if settings.TASK_RUNTIME_MODE == "multi" and settings.TASK_SERVICE.enabled:
            current = settings.TASK_SERVICE.get_record(state.task_id)
            return bool(current and current.cancel_requested)
        return False

    result = run_agent(
        task=state.task,
        model=_build_model(),
        policy=settings.POLICY,
        sandbox=_build_sandbox(),
        trace=ctx.trace,
        task_id=state.task_id,
        approver=build_approver(state),
        emit=_emit_event,
        require_claims=os.environ.get("MOCK_MODEL", "0") != "1",
        should_cancel=_should_cancel,
    )
    ctx.final_status = result.status
    ctx.final_summary = result.summary
    ctx.final_error = result.error


def _record_run_failure(ctx: RunContext, exc: Exception) -> None:
    ctx.final_status = "failed"
    ctx.final_error = f"{type(exc).__name__}: {exc}"
    if ctx.trace is None:
        return
    try:
        already = any(ev.get("event") == "task.failed" for ev in ctx.trace.read())
    except Exception:  # noqa: BLE001
        already = False
    if not already:
        try:
            ctx.trace.append(task_id=ctx.state.task_id, actor="system",
                             event="task.failed",
                             payload={"error": ctx.final_error})
        except Exception:  # noqa: BLE001
            pass


def _finalize_run(ctx: RunContext) -> None:
    state = ctx.state
    if ctx.heartbeat_stop is not None:
        ctx.heartbeat_stop.set()
    if ctx.deadline_timer is not None:
        ctx.deadline_timer.cancel()
    # Seal before publishing the terminal status so readers never see a
    # completed task whose anchor was still missing.
    if ctx.trace is not None:
        try:
            ctx.trace.seal(
                settings.TRACES / f"{state.trace_id}.anchor.json",
                hmac_key=os.environ.get("TRACE_HMAC_KEY") or None,
                ed25519_private_key=settings.TRACE_ED25519_KEY,
            )
        except Exception as exc:  # noqa: BLE001 - surface in state, do not crash
            ctx.final_status = "failed"
            ctx.final_error = ctx.final_error or (
                f"anchor_seal_failed: {type(exc).__name__}: {exc}"
            )
    persisted = settings.TASK_SERVICE.finish(
        state.task_id, TaskStatus(ctx.final_status),
        summary=ctx.final_summary, error=ctx.final_error,
    )
    if settings.TASK_SERVICE.enabled:
        if persisted is None or not persisted.terminal:
            # Never publish a successful terminal state when the DB write did
            # not happen; a later recovery can still repair the durable row.
            ctx.final_status = "failed"
            ctx.final_error = ctx.final_error or "terminal state was not persisted"
        elif persisted.status.value != ctx.final_status:
            # Another recovery/transition already owns the durable terminal
            # state; mirror it rather than claiming the local outcome.
            ctx.final_status = persisted.status.value
            ctx.final_error = ctx.final_error or persisted.error
    state.status = ctx.final_status
    state.summary = ctx.final_summary
    state.error = ctx.final_error
    task_state._release_exec_slot()


def run_task(state: TaskState, *, already_claimed: bool = False,
             slot_held: bool = False) -> None:
    """Run one task while guaranteeing terminal state and slot release."""
    ctx = _prepare_run(state, already_claimed=already_claimed, slot_held=slot_held)
    if ctx is None:
        return
    try:
        _start_run_controls(ctx)
        _open_trace(ctx)
        _execute_task(ctx)
    except Exception as exc:  # noqa: BLE001 - any failure must become task state + trace
        _record_run_failure(ctx, exc)
    finally:
        _finalize_run(ctx)



