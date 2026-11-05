"""Task-store schema constants and ordered migrations.

Kept separate from TaskStore so the CRUD/state-machine surface and the
schema evolution history can be reviewed independently.
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 5

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

_EVENTS = """
CREATE TABLE IF NOT EXISTS task_events (
    task_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    event_json  TEXT NOT NULL,
    created_at  REAL NOT NULL,
    PRIMARY KEY (task_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_task_events_task_seq
    ON task_events(task_id, seq);
"""

_LIMITS = """
CREATE TABLE IF NOT EXISTS rate_hits (
    key  TEXT NOT NULL,
    ts   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate_hits_key_ts ON rate_hits(key, ts);
CREATE TABLE IF NOT EXISTS sse_leases (
    token       TEXT PRIMARY KEY,
    created_at  REAL NOT NULL
);
"""


def _migration_v1(conn: sqlite3.Connection) -> None:
    conn.execute(_TASKS)
    conn.execute(_APPROVALS)
    conn.executescript(_INDEXES)


def _migration_v2(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "updated_at" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN updated_at REAL")
    conn.execute(
        "UPDATE tasks SET updated_at = COALESCE(finished_at, started_at, created_at)"
    )


def _migration_v3(conn: sqlite3.Connection) -> None:
    conn.executescript(_EVENTS)


def _migration_v4(conn: sqlite3.Connection) -> None:
    conn.executescript(_LIMITS)


def _migration_v5(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rate_hits_ts ON rate_hits(ts)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sse_leases_created "
        "ON sse_leases(created_at)"
    )


# Ordered migration steps. Add a new function and bump SCHEMA_VERSION for any
# future schema change; never edit an already-released step.
_MIGRATIONS = {1: _migration_v1, 2: _migration_v2, 3: _migration_v3,
               4: _migration_v4, 5: _migration_v5}


def apply_schema(conn: sqlite3.Connection) -> None:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"task store schema {version} is newer than supported {SCHEMA_VERSION}"
        )
    for target in range(version + 1, SCHEMA_VERSION + 1):
        migration = _MIGRATIONS.get(target)
        if migration is None:
            raise RuntimeError(f"missing task store migration {target}")
        migration(conn)
        conn.execute(f"PRAGMA user_version={target}")
