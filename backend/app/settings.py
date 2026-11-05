"""Configuration, paths, service singletons and runtime locks.

This module is the only place that reads process-wide settings. Other modules
import it as a module so tests can replace singletons (notably TASK_SERVICE).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.policy import PolicyEngine
from app.tasks import TaskService

APP_VERSION = "0.1.13"

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
TASK_RUNTIME_MODE = os.environ.get("TASK_RUNTIME_MODE", "single").lower()
TASK_EVENT_STORE = os.environ.get("TASK_EVENT_STORE", "memory").lower()
TASK_APPROVAL_STORE = os.environ.get("TASK_APPROVAL_STORE", "memory").lower()
if TASK_RUNTIME_MODE == "multi":
    TASK_EVENT_STORE = "db"
    TASK_APPROVAL_STORE = "db"

# Durable task runtime is single-writer by design. Acquire the exclusive lock
# BEFORE constructing/recovering TaskService: a second process must not mutate
# the first process's active tasks just because it later fails to get the lock.
_RUNTIME_LOCK_HANDLE: Any | None = None
_RUNTIME_LOCK_OK = True


def _acquire_runtime_lock() -> bool:
    global _RUNTIME_LOCK_HANDLE
    if TASK_RUNTIME_MODE == "multi" or TASK_STORE_MODE != "sqlite":
        return True
    try:
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
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

TASK_SERVICE = (
    TaskService.from_path(ARTIFACTS / "tasks.db",
                          recover=(TASK_RUNTIME_MODE != "multi" and _RUNTIME_LOCK_OK))
    if TASK_STORE_MODE == "sqlite"
    else TaskService()
)
POLICY = PolicyEngine.from_file()


TRACE_SIGNING_ERROR: str | None = None


def _load_trace_signing_key() -> Any | None:
    global TRACE_SIGNING_ERROR
    path = os.environ.get("TRACE_ED25519_PRIVATE_KEY_FILE")
    if not path:
        return None
    try:
        from app.trace.signing import load_private_key

        return load_private_key(path)
    except Exception as exc:  # noqa: BLE001 - expose failure instead of silently signing-less
        TRACE_SIGNING_ERROR = f"{type(exc).__name__}: {exc}"
        return None


TRACE_ED25519_KEY = _load_trace_signing_key()
TRACE_ED25519_PUBLIC = (
    TRACE_ED25519_KEY.public_key() if TRACE_ED25519_KEY is not None else None
)


# Concurrency / queue / lease.
TASK_CONCURRENCY = max(1, int(os.environ.get("TASK_CONCURRENCY", "1")))
TASK_WORKERS = max(1, int(os.environ.get("TASK_WORKERS", str(TASK_CONCURRENCY))))
TASK_WAITING_THREADS = max(0, int(os.environ.get("TASK_WAITING_THREADS", "4")))
TASK_QUEUE_MAX = max(TASK_CONCURRENCY, int(os.environ.get("TASK_QUEUE_MAX", "8")))
TASK_STATE_TTL_S = int(os.environ.get("TASK_STATE_TTL_S", "3600"))
TASK_LEASE_SECONDS = max(1.0, float(os.environ.get("TASK_LEASE_SECONDS", "60")))
TASK_HEARTBEAT_SECONDS = max(
    0.5, float(os.environ.get("TASK_HEARTBEAT_SECONDS",
                              str(min(20.0, TASK_LEASE_SECONDS / 3)))),
)
SSE_MAX_CONNECTIONS = int(os.environ.get("SSE_MAX_CONNECTIONS", "8"))
RATE_WINDOW_S = 60.0
_MISSING = object()










