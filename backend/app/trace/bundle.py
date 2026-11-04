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

    created = next((e for e in events if e.get("event") == "sandbox.created"), {})
    sandbox = (created.get("payload") or {}).get("sandbox") or {}
    if not sandbox:
        sandbox = {
            "backend": (created.get("payload") or {}).get("backend"),
            "runtime": (created.get("payload") or {}).get("runtime"),
        }
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
        "sandbox": {
            "backend": sandbox.get("backend"),
            "runtime": sandbox.get("runtime"),
            "container_id": sandbox.get("container_id"),
            "image_id": sandbox.get("image_id"),
            "limits": (created.get("payload") or {}).get("limits"),
        },
        "execution": {
            "results": [
                {
                    "seq": e.get("seq"),
                    "argv": e.get("exec", {}).get("argv"),
                    "exit_code": e.get("exec", {}).get("exit_code"),
                    "timed_out": e.get("exec", {}).get("timed_out"),
                    "stdout_sha256": e.get("exec", {}).get("stdout_sha256"),
                    "stderr_sha256": e.get("exec", {}).get("stderr_sha256"),
                }
                for e in events if e.get("event") == "sandbox.exec"
            ],
        },
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


def verify_bundle_dict(
    bundle: dict[str, Any], *, hmac_key: str | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": bundle.get("schema"),
        "status": "UNVERIFIABLE",
        "ok": False,
        "reason": None,
        "events": 0,
    }
    if bundle.get("schema") != BUNDLE_SCHEMA:
        result["reason"] = "schema_mismatch"
        return result
    events = ((bundle.get("trace") or {}).get("events")) or []
    result["events"] = len(events)
    if not events:
        result["reason"] = "no_events"
        return result

    chain_ok, bad_seq, reason = _verify_chain(events)
    if not chain_ok:
        result.update({"status": "FAILED", "reason": reason, "bad_seq": bad_seq})
        return result

    status = (bundle.get("task") or {}).get("status")
    kinds = [e.get("event") for e in events]
    last = kinds[-1] if kinds else None
    terminal = status in ("completed", "failed")
    if status == "running":
        result.update({"status": "PENDING", "reason": "task_running"})
        return result
    if terminal and not (("task.completed" in kinds) or ("task.failed" in kinds)):
        result.update({"status": "INCOMPLETE", "reason": "missing_terminal_event"})
        return result
    if last not in ("sandbox.destroy.confirmed", "sandbox.destroyed"):
        result.update({"status": "INCOMPLETE", "reason": "missing_confirmed_cleanup"})
        return result

    sandbox = bundle.get("sandbox") or {}
    missing = [k for k in ("runtime", "container_id", "image_id") if not sandbox.get(k)]
    if missing:
        result.update({"status": "INCOMPLETE", "reason": "missing_sandbox_fields:" + ",".join(missing)})
        return result
    if not (bundle.get("execution") or {}).get("results"):
        result.update({"status": "INCOMPLETE", "reason": "missing_execution_results"})
        return result

    anchor = (bundle.get("trace") or {}).get("anchor")
    final_hash = (bundle.get("trace") or {}).get("final_hash")
    if not anchor:
        result.update({"status": "INCOMPLETE", "reason": "missing_anchor"})
        return result
    if anchor.get("events") != len(events) or anchor.get("final_hash") != final_hash:
        result.update({"status": "FAILED", "reason": "anchor_mismatch"})
        return result
    if hmac_key:
        supplied = anchor.get("hmac")
        body = {k: v for k, v in anchor.items() if k != "hmac"}
        expected = hmac.new(hmac_key.encode("utf-8"),
                            canonical_json(body).encode("utf-8"),
                            hashlib.sha256).hexdigest()
        if not supplied or not hmac.compare_digest(supplied, expected):
            result.update({"status": "FAILED", "reason": "hmac_mismatch"})
            return result

    result.update({"status": "VERIFIED", "ok": True, "final_hash": final_hash,
                   "anchor": anchor})
    return result
