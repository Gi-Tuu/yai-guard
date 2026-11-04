# 审计事件模型：字段与 04 文档 §7 对齐。
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_trace_id() -> str:
    return "tr_" + secrets.token_hex(6)


def new_task_id() -> str:
    return "tk_" + secrets.token_hex(6)


# 允许的事件类型（未知类型拒绝，避免下游 UI 静默丢事件）
EVENT_TYPES = {
    "task.created",
    "plan.created",
    "agent.step",
    "tool.requested",
    "policy.decision",
    "approval.requested",
    "approval.resolved",
    "sandbox.created",
    "sandbox.exec",
    "file.changed",
    "resource.limit",
    "sandbox.destroyed",
    "sandbox.destroy.confirmed",
    "sandbox.destroy.unverified",
    "task.completed",
    "task.failed",
    "task.cancelled",
    "task.interrupted",
}


def build_event(
    *,
    trace_id: str,
    task_id: str,
    actor: str,
    event: str,
    payload: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if event not in EVENT_TYPES:
        raise ValueError(f"unknown event type: {event}")
    body: dict[str, Any] = {
        "schema_version": "1.0",
        "trace_id": trace_id,
        "task_id": task_id,
        "actor": actor,
        "event": event,
        "payload": payload or {},
    }
    if policy is not None:
        body["policy"] = policy
    if execution is not None:
        body["exec"] = execution
    return body
