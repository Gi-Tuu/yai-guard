# Evidence Bundle：把一次任务的执行证据打包成可离线校验的 JSON。
#
# 关键原则：
# - bundle 内嵌完整 trace 事件和 anchor，验证器不依赖原 API/进程/LLM；
# - 验证器重新计算哈希链、序号、终态、销毁确认、anchor，不信任 bundle 里的结论字段；
# - 缺少关键字段（container/image/runtime/cleanup/anchor）时返回 INCOMPLETE，绝不 VERIFIED。
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

from .store import canonical_json, verify_trace_file

BUNDLE_SCHEMA = "yai-guard-evidence/v1"


def _load_events(path: str | Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def _infer_status(events: list[dict[str, Any]]) -> str:
    kinds = [e.get("event") for e in events]
    if "task.completed" in kinds:
        return "completed"
    if "task.failed" in kinds:
        return "failed"
    return "running"


def _sandbox_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    created = next((e for e in events if e.get("event") == "sandbox.created"), {})
    payload = created.get("payload") or {}
    sandbox = payload.get("sandbox") or {}
    return {
        "backend": sandbox.get("backend", payload.get("backend")),
        "runtime": sandbox.get("runtime", payload.get("runtime")),
        "container_id": sandbox.get("container_id"),
        "image_id": sandbox.get("image_id"),
        "limits": payload.get("limits"),
    }


def _execution_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "seq": e.get("seq"),
            "argv": e.get("exec", {}).get("argv"),
            "exit_code": e.get("exec", {}).get("exit_code"),
            "timed_out": e.get("exec", {}).get("timed_out"),
            "stdout_sha256": e.get("exec", {}).get("stdout_sha256"),
            "stderr_sha256": e.get("exec", {}).get("stderr_sha256"),
        }
        for e in events
        if e.get("event") == "sandbox.exec"
    ]


def build_bundle(
    trace_path: str | Path,
    *,
    anchor_path: str | Path | None = None,
    hmac_key: str | None = None,
) -> dict[str, Any]:
    events = _load_events(trace_path)
    status = _infer_status(events)
    anchor = None
    if anchor_path and Path(anchor_path).exists():
        anchor = json.loads(Path(anchor_path).read_text(encoding="utf-8"))
    verification = verify_trace_file(
        trace_path,
        task_status=status,
        anchor_path=anchor_path,
        hmac_key=hmac_key,
        strict=status in ("completed", "failed"),
    )

    sandbox = _sandbox_from_events(events)
    cleanup = {}
    for e in events:
        if e.get("event") in ("sandbox.destroy.confirmed", "sandbox.destroy.unverified"):
            cleanup = e.get("payload") or {}

    return {
        "schema": BUNDLE_SCHEMA,
        "task": {
            "task_id": (events[0].get("task_id") if events else None),
            "trace_id": (events[0].get("trace_id") if events else None),
            "status": status,
        },
        "policy": {
            "version": 1,
            "decisions": [
                {"seq": e.get("seq"), "rule_id": e["policy"].get("rule_id"),
                 "effect": e["policy"].get("effect"), "reason": e["policy"].get("reason")}
                for e in events if e.get("event") == "policy.decision" and e.get("policy")
            ],
        },
        "sandbox": sandbox,
        "execution": {"results": _execution_from_events(events)},
        "approvals": [
            {"seq": e.get("seq"), "payload": e.get("payload")}
            for e in events if e.get("event") == "approval.resolved"
        ],
        "lifecycle": {
            "destroy": cleanup,
            "confirmed": bool(cleanup.get("cleanup", {}).get("verified")) if cleanup else False,
        },
        "trace": {
            "events": events,
            "final_hash": events[-1].get("hash") if events else None,
            "anchor": anchor,
        },
        "verification": verification,
    }


def _verify_chain(events: list[dict[str, Any]]) -> tuple[bool, int | None, str | None]:
    prev = "sha256:" + "0" * 64
    for index, event in enumerate(events, start=1):
        body = {k: v for k, v in event.items() if k != "hash"}
        expected = "sha256:" + hashlib.sha256(
            (prev + canonical_json(body)).encode("utf-8")
        ).hexdigest()
        if event.get("hash") != expected:
            return False, index, "hash_chain_mismatch"
        if event.get("seq") != index:
            return False, index, "sequence_mismatch"
        prev = event["hash"]
    return True, None, None


def _fail(
    result: dict[str, Any], status: str, reason: str, **extra: Any
) -> dict[str, Any]:
    result.update({"status": status, "ok": False, "reason": reason, **extra})
    return result


def verify_bundle_dict(
    bundle: dict[str, Any], *, hmac_key: str | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": bundle.get("schema"),
        "status": "UNVERIFIABLE",
        "ok": False,
        "content_ok": False,
        "signature_ok": None,
        "fully_verified": False,
        "verification_level": "none",
        "reason": None,
        "events": 0,
        "chain_ok": False,
        "metadata_ok": False,
        "final_hash_ok": False,
        "sandbox_ok": False,
        "execution_ok": False,
        "anchor_ok": False,
    }
    if bundle.get("schema") != BUNDLE_SCHEMA:
        return _fail(result, "UNVERIFIABLE", "schema_mismatch")
    events = ((bundle.get("trace") or {}).get("events")) or []
    result["events"] = len(events)
    if not events:
        return _fail(result, "UNVERIFIABLE", "no_events")

    chain_ok, bad_seq, reason = _verify_chain(events)
    result["chain_ok"] = chain_ok
    if not chain_ok:
        return _fail(result, "FAILED", reason or "hash_chain_mismatch", bad_seq=bad_seq)

    trace_ids = {event.get("trace_id") for event in events}
    task_ids = {event.get("task_id") for event in events}
    if len(trace_ids) != 1 or None in trace_ids or len(task_ids) != 1 or None in task_ids:
        return _fail(result, "FAILED", "event_identity_mismatch")
    trace_id = next(iter(trace_ids))
    task_id = next(iter(task_ids))

    task = bundle.get("task") or {}
    if task.get("trace_id") != trace_id or task.get("task_id") != task_id:
        return _fail(result, "FAILED", "task_metadata_mismatch")
    inferred_status = _infer_status(events)
    if task.get("status") != inferred_status:
        return _fail(result, "FAILED", "task_status_mismatch")
    result["metadata_ok"] = True

    kinds = [event.get("event") for event in events]
    if "task.completed" in kinds and "task.failed" in kinds:
        return _fail(result, "FAILED", "ambiguous_terminal_events")
    last = kinds[-1] if kinds else None
    if inferred_status == "running":
        result.update(
            {
                "status": "PENDING",
                "reason": "task_running",
                "content_ok": True,
                "verification_level": "pending",
            }
        )
        return result
    if last not in ("sandbox.destroy.confirmed", "sandbox.destroyed"):
        return _fail(result, "INCOMPLETE", "missing_confirmed_cleanup")

    expected_sandbox = _sandbox_from_events(events)
    sandbox = bundle.get("sandbox") or {}
    required = ("runtime", "container_id", "image_id")
    if any(not expected_sandbox.get(key) for key in required):
        return _fail(result, "INCOMPLETE", "missing_raw_sandbox_fields")
    if any(not sandbox.get(key) for key in required):
        return _fail(result, "INCOMPLETE", "missing_bundle_sandbox_fields")
    mismatched = [
        key for key in required if sandbox.get(key) != expected_sandbox.get(key)
    ]
    if mismatched:
        return _fail(
            result,
            "FAILED",
            "sandbox_metadata_mismatch:" + ",".join(mismatched),
        )
    for key in ("backend", "limits"):
        if key in sandbox and sandbox.get(key) != expected_sandbox.get(key):
            return _fail(result, "FAILED", f"sandbox_{key}_mismatch")
    result["sandbox_ok"] = True

    expected_execution = _execution_from_events(events)
    execution = (bundle.get("execution") or {}).get("results") or []
    if not expected_execution:
        return _fail(result, "INCOMPLETE", "missing_raw_execution_results")
    if not execution:
        return _fail(result, "INCOMPLETE", "missing_bundle_execution_results")
    if execution != expected_execution:
        return _fail(result, "FAILED", "execution_metadata_mismatch")
    result["execution_ok"] = True

    final_hash = (bundle.get("trace") or {}).get("final_hash")
    actual_final_hash = events[-1].get("hash")
    if not final_hash:
        return _fail(result, "INCOMPLETE", "missing_final_hash")
    if final_hash != actual_final_hash:
        return _fail(result, "FAILED", "final_hash_mismatch")
    result["final_hash_ok"] = True

    anchor = (bundle.get("trace") or {}).get("anchor")
    if not anchor:
        return _fail(result, "INCOMPLETE", "missing_anchor")
    if anchor.get("trace_id") != trace_id:
        return _fail(result, "FAILED", "anchor_trace_id_mismatch")
    if anchor.get("events") != len(events):
        return _fail(result, "FAILED", "anchor_event_count_mismatch")
    if anchor.get("final_hash") != actual_final_hash:
        return _fail(result, "FAILED", "anchor_final_hash_mismatch")
    result["anchor_ok"] = True

    if hmac_key:
        supplied = anchor.get("hmac")
        if not supplied:
            return _fail(
                result,
                "FAILED",
                "hmac_missing",
                signature_ok=False,
                content_ok=True,
            )
        body = {key: value for key, value in anchor.items() if key != "hmac"}
        expected = hmac.new(
            hmac_key.encode("utf-8"),
            canonical_json(body).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            return _fail(
                result,
                "FAILED",
                "hmac_mismatch",
                signature_ok=False,
                content_ok=True,
            )
        result["signature_ok"] = True
        result["fully_verified"] = True
        result["verification_level"] = "signed"
    else:
        result["signature_ok"] = None
        result["verification_level"] = "content_only"

    result.update(
        {
            "status": "VERIFIED",
            "ok": True,
            "content_ok": True,
            "final_hash": actual_final_hash,
            "anchor": anchor,
        }
    )
    return result
