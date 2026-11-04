"""In-memory task state, ownership, execution slots and bounded queue."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException

from app import settings


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
    worker_id: str = "app"


TASKS: dict[str, TaskState] = {}
PENDING: dict[tuple[str, str], dict[str, Any]] = {}
_LOCK = threading.Lock()

# 执行槽与排队：审批等待会释放执行槽，避免一个审批卡死全部任务。
_EXEC_SLOTS = threading.BoundedSemaphore(
    settings.TASK_WORKERS if settings.TASK_RUNTIME_MODE == "multi"
    else settings.TASK_CONCURRENCY
)
_TASK_EXECUTOR = ThreadPoolExecutor(
    max_workers=settings.TASK_CONCURRENCY + settings.TASK_WAITING_THREADS,
    thread_name_prefix="yaig-task",
)
_QUEUE_LOCK = threading.Lock()
_QUEUED = 0
_SSE_SLOTS = threading.BoundedSemaphore(settings.SSE_MAX_CONNECTIONS)
_DISPATCH_LOCK = threading.Lock()
_DISPATCHED_QUEUED: set[str] = set()


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
        if _QUEUED >= settings.TASK_QUEUE_MAX:
            return False
        _QUEUED += 1
        return True


def _release_queue_slot() -> None:
    global _QUEUED
    with _QUEUE_LOCK:
        _QUEUED = max(0, _QUEUED - 1)


def queued_count() -> int:
    return _QUEUED


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
            and now - state.created_at > settings.TASK_STATE_TTL_S
        ]
        for task_id in stale:
            TASKS.pop(task_id, None)


def _owned_task(task_id: str, principal: str) -> TaskState:
    """任务不存在与无权访问都返回 404，避免通过状态码枚举别人的任务。"""
    state = TASKS.get(task_id)
    if settings.TASK_RUNTIME_MODE == "multi" and settings.TASK_SERVICE.enabled:
        # DB is the only authoritative source across workers.
        state = None
    if state is None and settings.TASK_SERVICE.enabled:
        record = settings.TASK_SERVICE.get_record(task_id)
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
