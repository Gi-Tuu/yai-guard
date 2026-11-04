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
    def __init__(self, store: TaskStore | None = None, *, error: str | None = None,
                 recover: bool = True) -> None:
        self.store = store
        self.mode = "sqlite" if store is not None else "memory"
        self._error = error
        self._degraded = store is None and error is not None
        if store is not None and recover:
            try:
                store.recover_interrupted()
                store.expire_pending_approvals()
            except Exception as exc:  # noqa: BLE001 - recovery is best-effort
                self._degraded = True
                self._error = f"recovery failed: {type(exc).__name__}: {exc}"

    @classmethod
    def from_path(cls, path: str | Path | None, *,
                  recover: bool = True) -> "TaskService":
        if path is None:
            return cls()
        try:
            return cls(TaskStore(path), recover=recover)
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
            if record is None or record.status is not TaskStatus.QUEUED:
                # Claim exactly once. Returning None prevents a second dispatcher
                # from running the same task concurrently.
                return None
            return self.store.transition(
                task_id, TaskStatus.RUNNING,
                started_at=time.time(), worker_id=worker_id,
                lease_until=time.time() + lease_seconds,
                expected_version=record.version,
            )
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return None

    def heartbeat(self, task_id: str, *, worker_id: str = "app",
                  lease_seconds: float = 60.0) -> bool:
        if self.store is None:
            return False
        try:
            return self.store.heartbeat(
                task_id, worker_id=worker_id, lease_seconds=lease_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return False

    def request_cancel(self, task_id: str) -> bool:
        if self.store is None:
            return False
        try:
            return self.store.request_cancel(task_id)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return False

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

    def reconcile_terminal(self, task_id: str, status: TaskStatus | str, *,
                           summary: str = "", error: str | None = None) -> TaskRecord | None:
        if self.store is None:
            return None
        try:
            return self.store.reconcile_terminal(
                task_id, status, summary=summary, error=error,
            )
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return None

    def prune_terminal_before(self, cutoff: float) -> list[TaskRecord]:
        if self.store is None:
            return []
        try:
            return self.store.prune_terminal_before(cutoff)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return []

    def list_terminal_before(self, cutoff: float) -> list[TaskRecord]:
        if self.store is None:
            return []
        try:
            return self.store.list_terminal_before(cutoff)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return []

    def recover_expired(self) -> list[str]:
        if self.store is None:
            return []
        try:
            return self.store.recover_expired()
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return []

    def claim_next(self, *, worker_id: str, lease_seconds: float = 60.0) -> TaskRecord | None:
        if self.store is None:
            return None
        try:
            return self.store.claim_next(worker_id=worker_id, lease_seconds=lease_seconds)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return None

    def count_queued(self) -> int:
        if self.store is None:
            return 0
        try:
            return self.store.count_queued()
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return 0

    def expire_approvals_before(self, cutoff: float) -> int:
        if self.store is None:
            return 0
        try:
            return self.store.expire_approvals_before(cutoff)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return 0

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
    def append_event(self, task_id: str, event: dict[str, Any]) -> None:
        if self.store is None:
            return
        try:
            self.store.append_event(task_id, event)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)

    def list_events(self, task_id: str, *, after_seq: int = 0,
                    limit: int = 500) -> list[dict[str, Any]]:
        if self.store is None:
            return []
        try:
            return self.store.list_events(task_id, after_seq=after_seq, limit=limit)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return []

    def count_events(self, task_id: str) -> int:
        if self.store is None:
            return 0
        try:
            return self.store.count_events(task_id)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return 0

    def rate_limit(self, key: str, *, limit: int, window_s: float) -> int:
        if self.store is None:
            return 0
        try:
            return self.store.rate_limit(key, limit=limit, window_s=window_s)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return 0

    def acquire_sse(self, token: str, *, limit: int, ttl_s: float) -> bool:
        if self.store is None:
            return False
        try:
            return self.store.acquire_sse(token, limit=limit, ttl_s=ttl_s)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return False

    def renew_sse(self, token: str) -> None:
        if self.store is None:
            return
        try:
            self.store.renew_sse(token)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)

    def release_sse(self, token: str) -> None:
        if self.store is None:
            return
        try:
            self.store.release_sse(token)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)

    def queued_tasks(self) -> list[TaskRecord]:
        if self.store is None:
            return []
        try:
            return self.store.list_queued()
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return []

    def interrupted_tasks(self) -> list[TaskRecord]:
        if self.store is None:
            return []
        try:
            return self.store.list_tasks(status=TaskStatus.INTERRUPTED, limit=500)
        except Exception as exc:  # noqa: BLE001
            self._mark_degraded(exc)
            return []

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
