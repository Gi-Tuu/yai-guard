"""SQLite-backed task store for the durable task runtime (WP1 skeleton).

Not wired into the API yet. It is deliberately independent from FastAPI and
from the trace layer so it can be tested and migrated incrementally.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .models import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    ApprovalStatus,
    TaskRecord,
    TaskStatus,
    can_transition,
)

SCHEMA_VERSION = 1

_TASKS = """
CREATE TABLE IF NOT EXISTS tasks (
    id                TEXT PRIMARY KEY,
    trace_id          TEXT NOT NULL UNIQUE,
    owner             TEXT NOT NULL,
    prompt            TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN (
        'queued','running','waiting_approval','completed','failed',
        'cancelled','interrupted'
    )),
    summary           TEXT NOT NULL DEFAULT '',
    error             TEXT,
    cancel_requested  INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
    created_at        REAL NOT NULL,
    started_at        REAL,
    finished_at       REAL,
    lease_until       REAL,
    worker_id         TEXT,
    attempt           INTEGER NOT NULL DEFAULT 0,
    version           INTEGER NOT NULL DEFAULT 0
);
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_tasks_status_created
    ON tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_owner
    ON tasks(owner, created_at);
"""

_APPROVALS = """
CREATE TABLE IF NOT EXISTS approvals (
    task_id      TEXT NOT NULL,
    call_id      TEXT NOT NULL,
    owner        TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN (
        'pending','approved','rejected','expired','cancelled'
    )),
    created_at   REAL NOT NULL,
    expires_at   REAL NOT NULL,
    resolved_at  REAL,
    PRIMARY KEY (task_id, call_id)
);
"""


class TaskAlreadyExists(RuntimeError):
    pass


class TaskNotFound(KeyError):
    pass


class TaskStore:
    """Thread-safe SQLite task store.

    A single connection is used by the app process; WAL keeps readers cheap.
    Every write is inside a short transaction and guarded by ``RLock``.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path), check_same_thread=False, timeout=5.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._migrate()

    # ---------------------------------------------------------------- schema
    def _migrate(self) -> None:
        with self._lock, self._conn:
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"task store schema {version} is newer than supported {SCHEMA_VERSION}"
                )
            if version < 1:
                self._conn.execute(_TASKS)
                self._conn.execute(_APPROVALS)
                self._conn.executescript(_INDEXES)
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _record(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            id=row["id"],
            trace_id=row["trace_id"],
            owner=row["owner"],
            prompt=row["prompt"],
            status=TaskStatus(row["status"]),
            summary=row["summary"],
            error=row["error"],
            cancel_requested=bool(row["cancel_requested"]),
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            lease_until=row["lease_until"],
            worker_id=row["worker_id"],
            attempt=row["attempt"],
            version=row["version"],
        )

    def _get_locked(self, task_id: str) -> TaskRecord | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._record(row) if row else None

    # ---------------------------------------------------------------- create
    def create_task(
        self,
        *,
        task_id: str,
        trace_id: str,
        owner: str,
        prompt: str,
        now: float | None = None,
    ) -> TaskRecord:
        if not task_id or not trace_id:
            raise ValueError("task_id and trace_id are required")
        created = time.time() if now is None else now
        with self._lock, self._conn:
            try:
                self._conn.execute(
                    "INSERT INTO tasks (id, trace_id, owner, prompt, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (task_id, trace_id, owner, prompt, TaskStatus.QUEUED.value, created),
                )
            except sqlite3.IntegrityError as exc:
                raise TaskAlreadyExists(f"task/trace already exists: {task_id}") from exc
        record = self.get_task(task_id)
        assert record is not None
        return record

    # ------------------------------------------------------------------- read
    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._get_locked(task_id)

    def require_task(self, task_id: str) -> TaskRecord:
        record = self.get_task(task_id)
        if record is None:
            raise TaskNotFound(task_id)
        return record

    def list_tasks(
        self,
        *,
        owner: str | None = None,
        status: TaskStatus | str | None = None,
        limit: int = 50,
    ) -> list[TaskRecord]:
        clauses: list[str] = []
        values: list[Any] = []
        if owner is not None:
            clauses.append("owner = ?")
            values.append(owner)
        if status is not None:
            clauses.append("status = ?")
            values.append(TaskStatus(status).value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM tasks{where} ORDER BY created_at DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        return [self._record(row) for row in rows]

    # ------------------------------------------------------------- transition
    def transition(
        self,
        task_id: str,
        target: TaskStatus | str,
        *,
        summary: str | None = None,
        error: str | None = None,
        started_at: float | None = None,
        finished_at: float | None = None,
        lease_until: float | None = None,
        worker_id: str | None = None,
        cancel_requested: bool | None = None,
        expected_version: int | None = None,
    ) -> TaskRecord | None:
        """CAS transition. Returns None when the state no longer allows it."""
        destination = TaskStatus(target)
        with self._lock, self._conn:
            current = self._get_locked(task_id)
            if current is None:
                return None
            if not can_transition(current.status, destination):
                return None
            if expected_version is not None and current.version != expected_version:
                return None
            assignments = ["status = ?", "version = version + 1"]
            values: list[Any] = [destination.value]
            for column, value in (
                ("summary", summary),
                ("error", error),
                ("started_at", started_at),
                ("finished_at", finished_at),
                ("lease_until", lease_until),
                ("worker_id", worker_id),
            ):
                if value is not None:
                    assignments.append(f"{column} = ?")
                    values.append(value)
            if cancel_requested is not None:
                assignments.append("cancel_requested = ?")
                values.append(1 if cancel_requested else 0)
            if destination in TERMINAL_STATUSES and finished_at is None:
                assignments.append("finished_at = ?")
                values.append(time.time())
            values.append(task_id)
            cursor = self._conn.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",
                tuple(values),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_task(task_id)

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> TaskRecord | None:
        """Atomically claim the oldest queued task."""
        current = time.time() if now is None else now
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT id, version FROM tasks WHERE status = ? "
                    "ORDER BY created_at LIMIT 1",
                    (TaskStatus.QUEUED.value,),
                ).fetchone()
                if row is None:
                    self._conn.commit()
                    return None
                cursor = self._conn.execute(
                    "UPDATE tasks SET status = ?, worker_id = ?, lease_until = ?, "
                    "started_at = COALESCE(started_at, ?), attempt = attempt + 1, "
                    "version = version + 1 "
                    "WHERE id = ? AND status = ? AND version = ?",
                    (
                        TaskStatus.RUNNING.value,
                        worker_id,
                        current + lease_seconds,
                        current,
                        row["id"],
                        TaskStatus.QUEUED.value,
                        row["version"],
                    ),
                )
                if cursor.rowcount != 1:
                    self._conn.rollback()
                    return None
                self._conn.commit()
                return self._get_locked(row["id"])
            except Exception:
                self._conn.rollback()
                raise

    def heartbeat(
        self,
        task_id: str,
        *,
        worker_id: str,
        lease_seconds: float = 60.0,
        now: float | None = None,
    ) -> bool:
        current = time.time() if now is None else now
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE tasks SET lease_until = ? "
                "WHERE id = ? AND worker_id = ? AND status IN (?, ?)",
                (
                    current + lease_seconds,
                    task_id,
                    worker_id,
                    TaskStatus.RUNNING.value,
                    TaskStatus.WAITING_APPROVAL.value,
                ),
            )
            return cursor.rowcount == 1

    # ---------------------------------------------------------------- terminal
    def finish(
        self,
        task_id: str,
        status: TaskStatus | str,
        *,
        summary: str = "",
        error: str | None = None,
        expected_version: int | None = None,
    ) -> TaskRecord | None:
        destination = TaskStatus(status)
        if destination not in TERMINAL_STATUSES:
            raise ValueError(f"finish status must be terminal: {destination}")
        return self.transition(
            task_id,
            destination,
            summary=summary,
            error=error,
            expected_version=expected_version,
        )

    def recover_interrupted(
        self,
        *,
        reason: str = "interrupted by app restart",
        now: float | None = None,
    ) -> list[str]:
        """Mark stale active tasks interrupted. Never auto-reruns them."""
        finished = time.time() if now is None else now
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT id FROM tasks WHERE status IN (?, ?)",
                (TaskStatus.RUNNING.value, TaskStatus.WAITING_APPROVAL.value),
            ).fetchall()
            ids = [row["id"] for row in rows]
            for task_id in ids:
                self._conn.execute(
                    "UPDATE tasks SET status = ?, error = ?, finished_at = ?, "
                    "lease_until = NULL, worker_id = NULL, version = version + 1 "
                    "WHERE id = ? AND status IN (?, ?)",
                    (
                        TaskStatus.INTERRUPTED.value, reason, finished, task_id,
                        TaskStatus.RUNNING.value, TaskStatus.WAITING_APPROVAL.value,
                    ),
                )
        return ids

    def request_cancel(self, task_id: str, *, now: float | None = None) -> bool:
        with self._lock, self._conn:
            current = self._get_locked(task_id)
            if current is None or current.terminal:
                return False
            cursor = self._conn.execute(
                "UPDATE tasks SET cancel_requested = 1, version = version + 1 "
                "WHERE id = ? AND cancel_requested = 0 AND status NOT IN (?, ?, ?, ?)",
                (
                    task_id,
                    TaskStatus.COMPLETED.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED.value,
                    TaskStatus.INTERRUPTED.value,
                ),
            )
            return cursor.rowcount == 1

    # --------------------------------------------------------------- approvals
    def create_approval(
        self,
        *,
        task_id: str,
        call_id: str,
        owner: str,
        ttl_s: float,
        now: float | None = None,
    ) -> None:
        created = time.time() if now is None else now
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO approvals "
                "(task_id, call_id, owner, status, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    task_id, call_id, owner, ApprovalStatus.PENDING.value,
                    created, created + max(1.0, float(ttl_s)),
                ),
            )

    def resolve_approval(
        self,
        task_id: str,
        call_id: str,
        *,
        status: ApprovalStatus | str,
        now: float | None = None,
    ) -> bool:
        target = ApprovalStatus(status)
        if target not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED,
                          ApprovalStatus.EXPIRED, ApprovalStatus.CANCELLED):
            raise ValueError(f"invalid approval resolution: {target}")
        resolved = time.time() if now is None else now
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE approvals SET status = ?, resolved_at = ? "
                "WHERE task_id = ? AND call_id = ? AND status = ?",
                (target.value, resolved, task_id, call_id, ApprovalStatus.PENDING.value),
            )
            return cursor.rowcount == 1

    def get_approval(self, task_id: str, call_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM approvals WHERE task_id = ? AND call_id = ?",
                (task_id, call_id),
            ).fetchone()
        return dict(row) if row else None

    def pending_approvals(self, task_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if task_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM approvals WHERE status = ? ORDER BY created_at",
                    (ApprovalStatus.PENDING.value,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM approvals WHERE task_id = ? AND status = ? "
                    "ORDER BY created_at",
                    (task_id, ApprovalStatus.PENDING.value),
                ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ stats
    def count_by_status(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
            ).fetchall()
        return {row["status"]: int(row["n"]) for row in rows}


__all__ = [
    "ALLOWED_TRANSITIONS",
    "SCHEMA_VERSION",
    "TaskAlreadyExists",
    "TaskNotFound",
    "TaskRecord",
    "TaskStatus",
    "TaskStore",
    "ApprovalStatus",
]
