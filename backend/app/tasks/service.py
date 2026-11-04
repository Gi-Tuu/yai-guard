"""Feature-flagged TaskService bridge (WP2).

Memory remains the default and the existing API/runtime path. SQLite is an
opt-in mirror/durable store; any SQLite failure is surfaced as degraded health
instead of breaking task creation or execution.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .models import ApprovalStatus, TaskRecord, TaskStatus
from .store import TaskAlreadyExists, TaskStore


class TaskService:
    def __init__(self, store: TaskStore | None = None, *, error: str | None = None) -> None:
        self.store = store
        self.mode = "sqlite" if store is not None else "memory"
        self._error = error
        self._degraded = store is None and error is not None
        if store is not None:
            try:
                store.recover_interrupted()
            except Exception as exc:  # noqa: BLE001 - recovery is best-effort
                self._degraded = True
                self._error = f"recovery failed: {type(exc).__name__}: {exc}"

    @classmethod
    def from_path(cls, path: str | Path | None) -> "TaskService":
        if path is None:
            return cls()
        try:
            return cls(TaskStore(path))
        except Exception as exc:  # noqa: BLE001 - fall back to memory + degraded health
            return cls(None, error=f"{type(exc).__name__}: {exc}")

    @property
    def enabled(self) -> bool:
        return self.store is not None

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def error(self) -> str | None:
        return self._error

    def _mark_degraded(self, exc: Exception) -> None:
        self._degraded = True
        self._error = f"{type(exc).__name__}: {exc}"

    # ---------------------------------------------------------------- writes
    def create(self, *, task_id: str, trace_id: str, owner: str, prompt: str,
               now: float | None = None) -> TaskRecord | None:
        if self.store is None:
            return None
        try:
            return self.store.create_task(
                task_id=task_id, trace_id=trace_id, owner=owner, prompt=prompt, now=now
            )
        except TaskAlreadyExists:
            return None
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return None

    def mark_running(self, task_id: str, *, worker_id: str = "app",
                     lease_seconds: float = 60.0) -> TaskRecord | None:
        if self.store is None:
            return None
        try:
            record = self.store.get_task(task_id)
            if record is None:
                return None
            if record.status is TaskStatus.QUEUED:
                return self.store.transition(
                    task_id, TaskStatus.RUNNING,
                    started_at=time.time(), worker_id=worker_id,
                    lease_until=time.time() + lease_seconds,
                )
            return record
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return None

    def mark_waiting(self, task_id: str) -> TaskRecord | None:
        if self.store is None:
            return None
        try:
            record = self.store.get_task(task_id)
            if record is None or record.status is not TaskStatus.RUNNING:
                return record
            return self.store.transition(task_id, TaskStatus.WAITING_APPROVAL)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return None

    def mark_resumed(self, task_id: str) -> TaskRecord | None:
        if self.store is None:
            return None
        try:
            record = self.store.get_task(task_id)
            if record is None or record.status is not TaskStatus.WAITING_APPROVAL:
                return record
            return self.store.transition(
                task_id, TaskStatus.RUNNING,
                lease_until=time.time() + 60.0,
            )
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return None

    def finish(self, task_id: str, status: TaskStatus | str, *,
               summary: str = "", error: str | None = None) -> TaskRecord | None:
        if self.store is None:
            return None
        try:
            record = self.store.get_task(task_id)
            if record is None or record.terminal:
                return record
            destination = TaskStatus(status)
            if destination not in (
                TaskStatus.COMPLETED, TaskStatus.FAILED,
                TaskStatus.CANCELLED, TaskStatus.INTERRUPTED,
            ):
                return record
            return self.store.finish(
                task_id, destination, summary=summary, error=error,
            )
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return None

    # -------------------------------------------------------------- approvals
    def create_approval(self, *, task_id: str, call_id: str, owner: str,
                        ttl_s: float) -> bool:
        if self.store is None:
            return False
        try:
            self.store.create_approval(
                task_id=task_id, call_id=call_id, owner=owner, ttl_s=ttl_s,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return False

    def resolve_approval(self, task_id: str, call_id: str,
                         status: ApprovalStatus | str) -> bool:
        if self.store is None:
            return False
        try:
            return self.store.resolve_approval(task_id, call_id, status=status)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return False

    def get_approval(self, task_id: str, call_id: str) -> dict[str, Any] | None:
        if self.store is None:
            return None
        try:
            return self.store.get_approval(task_id, call_id)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return None

    def recover_interrupted(self) -> list[str]:
        if self.store is None:
            return []
        try:
            return self.store.recover_interrupted()
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return []

    # ---------------------------------------------------------------- reads
    def get_record(self, task_id: str) -> TaskRecord | None:
        if self.store is None:
            return None
        try:
            return self.store.get_task(task_id)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return None

    def pending_approvals(self, task_id: str | None = None) -> list[dict[str, Any]]:
        if self.store is None:
            return []
        try:
            return self.store.pending_approvals(task_id)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return []

    def health(self) -> dict[str, Any]:
        return {
            "ok": not self._degraded,
            "mode": self.mode,
            "degraded": self._degraded,
            "error": self._error,
        }

    def close(self) -> None:
        if self.store is not None:
            self.store.close()
