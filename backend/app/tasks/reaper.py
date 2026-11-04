"""SQLite-aware orphan reaper.

The controller only knows Docker; the app owns task state. The reaper runs on the
app side, compares sandbox containers with the durable task records, and only
removes containers that cannot belong to a healthy task:

- terminal tasks (completed/failed/cancelled/interrupted);
- queued tasks (a queued task must not have a container);
- running/waiting tasks whose lease has expired;
- unknown tasks older than the grace window.
"""
from __future__ import annotations

import time
from typing import Any

from .models import TaskStatus, is_terminal


def reap_orphans(
    task_service: Any,
    sandbox: Any,
    *,
    now: float | None = None,
    grace_s: float = 60.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    result: dict[str, Any] = {
        "ok": True,
        "checked": 0,
        "removed": [],
        "would_remove": [],
        "kept": [],
        "errors": [],
        "skipped_reason": None,
    }
    if not getattr(task_service, "enabled", False):
        result["skipped_reason"] = "task store disabled"
        return result

    try:
        containers = sandbox.list_containers()
    except Exception as exc:  # noqa: BLE001 - reaper must never crash the app
        result["ok"] = False
        result["errors"].append(f"{type(exc).__name__}: {exc}")
        return result

    for container in containers:
        result["checked"] += 1
        name = str(container.get("name") or "")
        task_id = str(container.get("task_id") or name.removeprefix("yaig-"))
        created_at = float(container.get("created_at") or 0.0)
        age = now - created_at if created_at else None
        if age is not None and age < grace_s:
            result["kept"].append({"task_id": task_id, "reason": "within_grace"})
            continue

        record = task_service.get_record(task_id)
        reason: str | None = None
        if record is None:
            reason = "unknown_task"
        elif is_terminal(record.status):
            reason = "terminal_task"
        elif record.status is TaskStatus.QUEUED:
            reason = "queued_task_has_container"
        elif record.status in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
            lease = record.lease_until
            if lease is not None and lease < now:
                task_service.finish(
                    task_id, TaskStatus.INTERRUPTED,
                    error="lease_expired_reaped",
                )
                reason = "lease_expired"
            else:
                result["kept"].append({"task_id": task_id, "reason": "active_lease"})
                continue
        else:
            reason = "inactive_task"

        if reason is None:
            continue
        if dry_run:
            result["would_remove"].append({"task_id": task_id, "name": name, "reason": reason})
            continue
        try:
            cleanup = sandbox.destroy(task_id)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"{task_id}: {type(exc).__name__}: {exc}")
            continue
        if cleanup.get("verified") is True:
            result["removed"].append({"task_id": task_id, "name": name, "reason": reason})
        else:
            result["errors"].append(f"{task_id}: destroy not verified: {cleanup}")

    result["ok"] = not result["errors"]
    return result
