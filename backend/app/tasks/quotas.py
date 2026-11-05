"""Global rate-limit and SSE-lease quota operations for TaskStore.

These are coordination primitives, not task CRUD; keeping them separate makes the
task state machine and the quota tables independently reviewable.
"""
from __future__ import annotations

import time


class QuotaMixin:
    def rate_limit(self, key: str, *, limit: int, window_s: float,
                   now: float | None = None) -> int:
        """Return 0 when allowed, otherwise Retry-After seconds."""
        current = time.time() if now is None else now
        with self._lock, self._conn:
            # Drop expired rows for every key, not only the current client:
            # one-off IPs would otherwise accumulate forever in this table.
            self._conn.execute(
                "DELETE FROM rate_hits WHERE ts < ?",
                (current - window_s,),
            )
            rows = self._conn.execute(
                "SELECT ts FROM rate_hits WHERE key = ? ORDER BY ts",
                (key,),
            ).fetchall()
            if len(rows) >= limit:
                retry = max(1, int(window_s - (current - float(rows[0]["ts"]))))
                return retry
            self._conn.execute("INSERT INTO rate_hits (key, ts) VALUES (?, ?)",
                               (key, current))
            return 0

    def acquire_sse(self, token: str, *, limit: int, ttl_s: float,
                    now: float | None = None) -> bool:
        current = time.time() if now is None else now
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sse_leases WHERE created_at < ?",
                               (current - ttl_s,))
            row = self._conn.execute("SELECT COUNT(*) AS n FROM sse_leases").fetchone()
            if int(row["n"]) >= limit:
                return False
            self._conn.execute(
                "INSERT OR REPLACE INTO sse_leases (token, created_at) VALUES (?, ?)",
                (token, current),
            )
            return True

    def renew_sse(self, token: str, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        with self._lock, self._conn:
            self._conn.execute("UPDATE sse_leases SET created_at = ? WHERE token = ?",
                               (current, token))

    def release_sse(self, token: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM sse_leases WHERE token = ?", (token,))
