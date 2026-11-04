"""Task-level domain model for the durable task runtime.

This is intentionally independent of FastAPI and of the trace layer. The trace
JSONL/anchor remains the event-level source of truth; SQLite stores only task
metadata and coordination state.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_STATUSES = frozenset({
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.INTERRUPTED,
})

ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.QUEUED: frozenset({
        TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED,
        TaskStatus.INTERRUPTED,
    }),
    TaskStatus.RUNNING: frozenset({
        TaskStatus.WAITING_APPROVAL, TaskStatus.COMPLETED, TaskStatus.FAILED,
        TaskStatus.CANCELLED, TaskStatus.INTERRUPTED,
    }),
    TaskStatus.WAITING_APPROVAL: frozenset({
        TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED,
        TaskStatus.INTERRUPTED,
    }),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.INTERRUPTED: frozenset(),
}


def is_terminal(status: TaskStatus | str) -> bool:
    try:
        return TaskStatus(status) in TERMINAL_STATUSES
    except ValueError:
        return False


def can_transition(current: TaskStatus | str, target: TaskStatus | str) -> bool:
    try:
        source = TaskStatus(current)
        destination = TaskStatus(target)
    except ValueError:
        return False
    return destination in ALLOWED_TRANSITIONS.get(source, frozenset())


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskRecord:
    id: str
    trace_id: str
    owner: str
    prompt: str
    status: TaskStatus
    summary: str = ""
    error: str | None = None
    cancel_requested: bool = False
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    lease_until: float | None = None
    worker_id: str | None = None
    attempt: int = 0
    version: int = 0

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def active(self) -> bool:
        return self.status in (
            TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL,
        )

    def to_view(self) -> dict[str, Any]:
        """API-compatible view; keep existing field names stable."""
        return {
            "task_id": self.id,
            "trace_id": self.trace_id,
            "status": self.status.value,
            "summary": self.summary,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "attempt": self.attempt,
        }
