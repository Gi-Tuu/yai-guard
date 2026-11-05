"""Background maintenance: reaper, retention, requeue, multi-worker recovery."""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from app.tasks import TaskStatus
from app.tasks.reaper import reap_orphans as reap_task_orphans
from app.trace import TraceStore
from app import settings
from app import task_state
from app import quiesce
from app.task_runner import _build_sandbox, run_task

_log = logging.getLogger(__name__)

_REAPER_STARTED = False
_REQUEUE_STARTED = False
_RETENTION_STARTED = False
_MULTI_WORKERS_STARTED = False
_STATE_PRUNER_STARTED = False
_MAINTENANCE_LOCK_HANDLE = None


def _state_prune_loop() -> None:
    while True:
        time.sleep(60)
        if quiesce.is_quiesced():
            continue
        try:
            task_state._prune_tasks()
        except Exception:  # noqa: BLE001
            pass


def _start_state_pruner() -> None:
    global _STATE_PRUNER_STARTED
    if _STATE_PRUNER_STARTED:
        return
    _STATE_PRUNER_STARTED = True
    threading.Thread(target=_state_prune_loop, name="task-state-pruner",
                     daemon=True).start()


def _run_orphan_reaper(*, grace_s: float = 60.0,
                       dry_run: bool = False) -> dict[str, Any]:
    return reap_task_orphans(
        settings.TASK_SERVICE, _build_sandbox(), grace_s=grace_s, dry_run=dry_run,
    )


def _prune_old_state() -> dict[str, Any]:
    """Delete terminal tasks/traces older than TASK_RETENTION_DAYS (0 disables)."""
    summary: dict[str, Any] = {"ok": True, "removed": [], "traces": [],
                               "retention_days": 0, "skipped_reason": None}
    if not settings.TASK_SERVICE.enabled:
        summary["skipped_reason"] = "task store disabled"
        return summary
    try:
        days = float(os.environ.get("TASK_RETENTION_DAYS", "30"))
    except ValueError:
        days = 30.0
    summary["retention_days"] = days
    if days <= 0:
        summary["skipped_reason"] = "retention disabled"
        return summary
    records = settings.TASK_SERVICE.prune_terminal_before(time.time() - days * 86400)
    for record in records:
        summary["removed"].append(record.id)
        for suffix in (".jsonl", ".anchor.json"):
            path = settings.TRACES / f"{record.trace_id}{suffix}"
            try:
                if path.exists():
                    path.unlink()
                    summary["traces"].append(str(path))
            except OSError:
                summary["ok"] = False
    return summary


def _retention_loop() -> None:
    interval = max(60, int(os.environ.get("RETENTION_INTERVAL_S", "21600")))
    while True:
        time.sleep(interval)
        if quiesce.is_quiesced():
            continue
        try:
            _prune_old_state()
        except Exception:  # noqa: BLE001 - retention must not crash the app
            _log.warning("retention failed", exc_info=True)


def _multi_worker_loop(worker_id: str) -> None:
    while True:
        if not task_state._EXEC_SLOTS.acquire(blocking=False):
            time.sleep(0.5)
            continue
        if quiesce.is_quiesced():
            task_state._release_exec_slot()
            time.sleep(0.5)
            continue
        record = settings.TASK_SERVICE.claim_next(
            worker_id=worker_id, lease_seconds=settings.TASK_LEASE_SECONDS,
        )
        if record is None:
            task_state._release_exec_slot()
            time.sleep(0.5)
            continue
        state = task_state.TaskState(
            task_id=record.id,
            trace_id=record.trace_id,
            task=record.prompt,
            owner=record.owner,
            status=record.status.value,
            worker_id=worker_id,
        )
        with task_state._LOCK:
            task_state.TASKS[record.id] = state
        try:
            run_task(state, already_claimed=True, slot_held=True)
        except Exception:  # noqa: BLE001 - one task must not kill the worker
            task_state._release_exec_slot()


def _try_maintenance_lock() -> bool:
    """Only one process performs recovery/evidence maintenance in multi mode."""
    global _MAINTENANCE_LOCK_HANDLE
    if _MAINTENANCE_LOCK_HANDLE is not None:
        return True
    try:
        import fcntl
    except ImportError:
        return True
    try:
        handle = open(settings.ARTIFACTS / "task-maintenance.lock", "a+",
                      encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _MAINTENANCE_LOCK_HANDLE = handle
        return True
    except OSError:
        return False


def _multi_recovery_loop() -> None:
    next_prune = 0.0
    while True:
        time.sleep(max(5, int(os.environ.get("MULTI_RECOVERY_INTERVAL_S", "15"))))
        if quiesce.is_quiesced():
            continue
        if not _try_maintenance_lock():
            continue
        try:
            recovered = settings.TASK_SERVICE.recover_expired()
            settings.TASK_SERVICE.expire_approvals_before(time.time())
            if recovered:
                try:
                    _run_orphan_reaper(grace_s=0)
                except Exception:  # noqa: BLE001
                    pass
            _record_interrupted_events()
            if time.time() >= next_prune:
                _prune_old_state()
                next_prune = time.time() + max(
                    60, int(os.environ.get("RETENTION_INTERVAL_S", "21600")),
                )
        except Exception:  # noqa: BLE001
            pass


def _reaper_loop() -> None:
    interval = max(30, int(os.environ.get("REAPER_INTERVAL_S", "300")))
    while True:
        time.sleep(interval)
        if quiesce.is_quiesced():
            continue
        try:
            _run_orphan_reaper()
        except Exception:  # noqa: BLE001 - background reaper must not crash the app
            _log.warning("orphan reaper failed", exc_info=True)


def _record_interrupted_events() -> list[str]:
    """Make recovery visible in the trace, idempotently, for every interrupted task."""
    recorded: list[str] = []
    if not settings.TASK_SERVICE.enabled:
        return recorded
    for record in settings.TASK_SERVICE.interrupted_tasks():
        path = settings.TRACES / f"{record.trace_id}.jsonl"
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
                    event = terminal_events[0]
                    payload = event.get("payload") or {}
                    target = {
                        "task.completed": TaskStatus.COMPLETED,
                        "task.failed": TaskStatus.FAILED,
                        "task.cancelled": TaskStatus.CANCELLED,
                    }[event["event"]]
                    reconcile_error = payload.get("error") or payload.get("reason")
                    if (event["event"] == "task.completed"
                            and any(e.get("event") == "sandbox.destroy.unverified"
                                    for e in events)):
                        target = TaskStatus.FAILED
                        reconcile_error = "cleanup_unverified"
                    settings.TASK_SERVICE.reconcile_terminal(
                        record.id, target,
                        summary=str(payload.get("summary", "")),
                        error=reconcile_error,
                    )
                    recorded.append(record.id)
                continue
            if any(e.get("event") == "task.interrupted" for e in events):
                continue
            trace.append(
                task_id=record.id, actor="system", event="task.interrupted",
                payload={"reason": record.error or "app_restart",
                         "recovered_from": "durable_task_store"},
            )
            trace.seal(
                settings.TRACES / f"{record.trace_id}.anchor.json",
                hmac_key=os.environ.get("TRACE_HMAC_KEY") or None,
                ed25519_private_key=settings.TRACE_ED25519_KEY,
            )
            recorded.append(record.id)
        except Exception:  # noqa: BLE001 - one bad trace must not break startup
            continue
    return recorded


def _requeue_memory_tasks() -> dict[str, Any]:
    """Recover queued in-memory tasks after a quiesce marker is removed.

    The memory runtime has no DB queue to redispatch from, so a task whose
    dispatch was cleared by quiesce must be picked up here.
    """
    summary: dict[str, Any] = {"requeued": [], "cancelled": [], "skipped": []}
    with task_state._LOCK:
        candidates = [
            state for state in task_state.TASKS.values()
            if state.status == "queued"
        ]
    for state in candidates:
        with task_state._DISPATCH_LOCK:
            if state.task_id in task_state._DISPATCHED_QUEUED:
                summary["skipped"].append(
                    {"task_id": state.task_id, "reason": "already_dispatched"},
                )
                continue
            task_state._DISPATCHED_QUEUED.add(state.task_id)
        if not task_state._reserve_queue_slot():
            with task_state._DISPATCH_LOCK:
                task_state._DISPATCHED_QUEUED.discard(state.task_id)
            summary["skipped"].append(
                {"task_id": state.task_id, "reason": "queue_full"},
            )
            continue
        try:
            task_state._TASK_EXECUTOR.submit(run_task, state)
        except Exception as exc:  # noqa: BLE001
            task_state._release_queue_slot()
            with task_state._DISPATCH_LOCK:
                task_state._DISPATCHED_QUEUED.discard(state.task_id)
            summary["skipped"].append({
                "task_id": state.task_id,
                "reason": f"{type(exc).__name__}: {exc}",
            })
            continue
        summary["requeued"].append(state.task_id)
    return summary


def _requeue_queued_tasks() -> dict[str, Any]:
    """Re-dispatch queued tasks; dispatched-but-not-started ones are not duplicated."""
    summary: dict[str, Any] = {"requeued": [], "cancelled": [], "skipped": []}
    if quiesce.is_quiesced():
        summary["skipped"].append("quiesced")
        return summary
    if not settings.TASK_SERVICE.enabled:
        return _requeue_memory_tasks()
    if os.environ.get("REQUEUE_ON_STARTUP", "1") == "0":
        summary["skipped"].append("disabled_by_env")
        return summary
    for record in settings.TASK_SERVICE.queued_tasks():
        with task_state._DISPATCH_LOCK:
            already_dispatched = record.id in task_state._DISPATCHED_QUEUED
        if already_dispatched:
            summary["skipped"].append({"task_id": record.id, "reason": "already_dispatched"})
            continue
        if record.cancel_requested:
            settings.TASK_SERVICE.finish(
                record.id, TaskStatus.CANCELLED,
                summary="cancelled before start",
            )
            summary["cancelled"].append(record.id)
            continue
        state = task_state.TaskState(
            task_id=record.id,
            trace_id=record.trace_id,
            task=record.prompt,
            owner=record.owner,
        )
        with task_state._LOCK:
            task_state.TASKS.setdefault(record.id, state)
        if not task_state._reserve_queue_slot():
            summary["skipped"].append({"task_id": record.id, "reason": "queue_full"})
            continue
        with task_state._DISPATCH_LOCK:
            task_state._DISPATCHED_QUEUED.add(record.id)
        try:
            task_state._TASK_EXECUTOR.submit(run_task, state)
        except Exception as exc:  # noqa: BLE001
            task_state._release_queue_slot()
            with task_state._DISPATCH_LOCK:
                task_state._DISPATCHED_QUEUED.discard(record.id)
            task_state.TASKS.pop(record.id, None)
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
            _log.warning("queued requeue failed", exc_info=True)


def _start_multi_runtime() -> None:
    global _REAPER_STARTED, _MULTI_WORKERS_STARTED
    if os.environ.get("SANDBOX_DRIVER", "fake") == "remote":
        try:
            _run_orphan_reaper(grace_s=0)
        except Exception:  # noqa: BLE001
            pass
        if os.environ.get("ORPHAN_REAPER", "1") != "0" and not _REAPER_STARTED:
            _REAPER_STARTED = True
            threading.Thread(target=_reaper_loop, name="orphan-reaper",
                             daemon=True).start()
    if not _MULTI_WORKERS_STARTED:
        _MULTI_WORKERS_STARTED = True
        for index in range(settings.TASK_WORKERS):
            worker_id = f"{os.getpid()}-{index}"
            threading.Thread(target=_multi_worker_loop, args=(worker_id,),
                             name=f"multi-worker-{index}", daemon=True).start()
        threading.Thread(target=_multi_recovery_loop, name="multi-recovery",
                         daemon=True).start()


def _start_requeue_loop() -> None:
    global _REQUEUE_STARTED
    if os.environ.get("REQUEUE_ON_STARTUP", "1") == "0":
        return
    if _REQUEUE_STARTED:
        return
    _REQUEUE_STARTED = True
    threading.Thread(target=_requeue_loop, name="queued-requeue",
                     daemon=True).start()


def _start_single_runtime() -> None:
    global _REAPER_STARTED, _RETENTION_STARTED
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
    _start_requeue_loop()
    try:
        _record_interrupted_events()
    except Exception:  # noqa: BLE001 - do not block startup
        pass
    try:
        _prune_old_state()
    except Exception:  # noqa: BLE001 - do not block startup
        pass
    if (os.environ.get("TASK_RETENTION_DAYS", "30") != "0"
            and not _RETENTION_STARTED):
        _RETENTION_STARTED = True
        threading.Thread(target=_retention_loop, name="task-retention",
                         daemon=True).start()


def _start_fake_runtime() -> None:
    try:
        from app.sandbox.remote_backend import RemoteSandbox

        RemoteSandbox(
            os.environ.get("SANDBOX_BASE_URL", "http://127.0.0.1:9000"),
            os.environ.get("SANDBOX_TOKEN", ""),
            timeout_s=5.0,
        ).reap_orphans()
    except Exception:  # noqa: BLE001 - startup cleanup is best-effort
        pass


def start_background_tasks() -> None:
    """Startup dispatcher; no route or app object is needed here."""
    if not settings._RUNTIME_LOCK_OK:
        return
    if settings.TASK_STORE_MODE == "sqlite" and settings.TASK_SERVICE.fatal:
        return
    _start_state_pruner()
    if settings.TASK_SERVICE.enabled and settings.TASK_RUNTIME_MODE == "multi":
        _start_multi_runtime()
        return
    if settings.TASK_SERVICE.enabled:
        # SQLite single mode and fake/docker single mode both need the
        # memory/DB requeue and retention loops; do not gate them on remote.
        _start_single_runtime()
        return
    if os.environ.get("SANDBOX_DRIVER", "fake") == "remote":
        _start_fake_runtime()
    # Memory mode has no DB queue, but a quiesce can still strand an in-memory
    # task; always start the requeue loop for single-process mode.
    _start_requeue_loop()

