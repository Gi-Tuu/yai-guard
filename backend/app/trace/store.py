# Append-only 审计存储 + 哈希链。
#
# 每条事件：
#   hash = sha256(prev_hash + canonical_json(event 去掉 hash))
# 任何一条被改动/删除，verify() 都会在该条失败。
#
# 注意：哈希链只能证明「这份日志在生成后未被篡改」，不能证明「日志本身完整记录了
# 所有操作」——后者依靠 Agent 只通过 TraceStore 写审计这一代码约束。
from __future__ import annotations

import hashlib
import hmac
import json
import threading
from pathlib import Path
from typing import Any

from .events import build_event, utc_now_iso
from .redact import redact

GENESIS = "sha256:" + "0" * 64


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _read_events(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    if not path.exists():
        return [], "missing"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [], f"unreadable: {exc}"
    events: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            return events, "malformed_json"
    if not events:
        return [], "empty"
    return events, None


def _verify_chain(events: list[dict[str, Any]]) -> dict[str, Any]:
    """链完整性 + 严格序号连续性（seq 必须从 1 开始逐条 +1）。"""
    prev = GENESIS
    chain_ok = True
    sequence_ok = True
    bad_seq: int | None = None
    for index, ev in enumerate(events, start=1):
        if ev.get("seq") != index:
            sequence_ok = False
            bad_seq = bad_seq if bad_seq is not None else ev.get("seq")
        stored_prev = ev.get("prev_hash")
        if stored_prev != prev:
            chain_ok = False
            bad_seq = bad_seq if bad_seq is not None else ev.get("seq")
            break
        body = {k: v for k, v in ev.items() if k != "hash"}
        expect = "sha256:" + hashlib.sha256((prev + canonical_json(body)).encode("utf-8")).hexdigest()
        if ev.get("hash") != expect:
            chain_ok = False
            bad_seq = bad_seq if bad_seq is not None else ev.get("seq")
            break
        prev = ev["hash"]
    return {"chain_ok": chain_ok, "sequence_ok": sequence_ok, "bad_seq": bad_seq,
            "final_hash": prev if chain_ok else None}


def verify_trace_file(
    path: str | Path,
    *,
    task_status: str | None = None,
    anchor_path: str | Path | None = None,
    hmac_key: str | None = None,
    public_key: Any | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """结构化校验：区分 chain / sequence / completeness / cleanup / anchor / HMAC。

    strict=True 时（终态任务）：
    - 必须有 anchor，且 anchor 的 events/final_hash 与文件一致；
    - 若配置了 hmac_key，anchor 必须带可验证 HMAC；
    - 终态任务必须有 confirmed（或旧版 destroyed）销毁事件；
    - 缺失 Anchor → INCOMPLETE（绝不返回 VERIFIED）。
    运行中任务返回 PENDING。
    """
    p = Path(path)
    events, error = _read_events(p)
    terminal = task_status in ("completed", "failed", "cancelled", "interrupted")
    result: dict[str, Any] = {
        "ok": False, "status": "UNVERIFIABLE", "reason": error, "events": len(events),
        "chain_ok": False, "sequence_ok": False, "completeness_ok": None,
        "cleanup_ok": None, "anchor_ok": None, "authentic": None,
        "signature_alg": None, "ed25519_ok": None,
        "bad_seq": None, "final_hash": None, "strict": strict,
    }
    if task_status in ("queued", "running"):
        result["status"] = "PENDING"
        return result
    if error:
        result["status"] = "UNVERIFIABLE" if error in ("missing", "unreadable") else "FAILED"
        return result

    chain = _verify_chain(events)
    result.update(chain)
    result["final_hash"] = events[-1].get("hash")

    kinds = [ev.get("event") for ev in events]
    last = kinds[-1] if kinds else None
    if last == "sandbox.destroy.unverified":
        result["cleanup_ok"] = False
    elif last == "sandbox.destroy.confirmed":
        result["cleanup_ok"] = True
    elif last == "sandbox.destroyed":
        # Legacy event only proves that a destroy was attempted. Strict
        # verification must not turn it into a confirmed cleanup.
        result["cleanup_ok"] = None
    if task_status == "completed":
        result["completeness_ok"] = bool(
            "task.completed" in kinds and last in ("sandbox.destroy.confirmed", "sandbox.destroyed")
        )
    elif task_status == "failed":
        result["completeness_ok"] = "task.failed" in kinds
    elif task_status == "cancelled":
        result["completeness_ok"] = bool(
            "task.cancelled" in kinds and last == "sandbox.destroy.confirmed"
        )
    elif task_status == "interrupted":
        # Recovery proves the interruption; a confirmed cleanup is nice but not
        # required because the crashed process never got to write it.
        result["completeness_ok"] = "task.interrupted" in kinds

    anchor_present = bool(anchor_path) and Path(anchor_path).exists()
    if anchor_path is not None and not anchor_present:
        result["anchor_ok"] = False
        result["reason"] = "anchor_missing"
    elif anchor_present:
        try:
            anchor = json.loads(Path(anchor_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            result["anchor_ok"] = False
            result["reason"] = "anchor_malformed"
        else:
            anchor_ok = (
                anchor.get("events") == len(events)
                and anchor.get("final_hash") == result["final_hash"]
            )
            result["anchor_ok"] = anchor_ok
            if not anchor_ok:
                result["reason"] = "anchor_mismatch"
            if hmac_key:
                supplied = anchor.get("hmac")
                body = {k: v for k, v in anchor.items() if k != "hmac"}
                expect = hmac.new(hmac_key.encode("utf-8"),
                                  canonical_json(body).encode("utf-8"),
                                  hashlib.sha256).hexdigest()
                if not supplied:
                    result["authentic"] = False
                    result["reason"] = result["reason"] or "hmac_missing"
                else:
                    result["authentic"] = hmac.compare_digest(supplied, expect)
                    if not result["authentic"]:
                        result["reason"] = "hmac_mismatch"
            else:
                result["authentic"] = None
            if public_key is not None:
                from .signing import verify_anchor_signature

                result["signature_alg"] = "ed25519"
                result["ed25519_ok"] = verify_anchor_signature(anchor, public_key)
                if not result["ed25519_ok"]:
                    result["authentic"] = False
                    result["reason"] = result["reason"] or "ed25519_mismatch"

    if not result["chain_ok"] or not result["sequence_ok"]:
        result["status"] = "FAILED"
    elif result["completeness_ok"] is False:
        result["status"] = "INCOMPLETE"
    elif result["cleanup_ok"] is False:
        result["status"] = "INCOMPLETE"
    elif strict and terminal:
        if anchor_path is None or result["anchor_ok"] is not True:
            result["status"] = "INCOMPLETE" if result["anchor_ok"] is not False else "FAILED"
        elif result["cleanup_ok"] is not True and task_status != "interrupted":
            result["status"] = "INCOMPLETE"
        elif hmac_key and result["authentic"] is not True:
            result["status"] = "FAILED"
        elif public_key is not None and result["ed25519_ok"] is not True:
            result["status"] = "FAILED"
        else:
            result["status"] = "VERIFIED"
    elif result["anchor_ok"] is False or result["authentic"] is False:
        result["status"] = "FAILED"
    else:
        result["status"] = "VERIFIED"

    result["ok"] = result["status"] == "VERIFIED"
    return result


def verify_file(path: str | Path) -> tuple[bool, int | None]:
    """兼容旧调用：只做链校验，返回 (ok, bad_seq)。"""
    result = verify_trace_file(path)
    return result["chain_ok"], result["bad_seq"]


class TraceError(RuntimeError):
    pass


class TraceStore:
    """单文件 JSONL 审计存储（每任务一个文件）。线程安全。"""

    def __init__(self, path: str | Path, trace_id: str):
        self.path = Path(path)
        self.trace_id = trace_id
        self._lock = threading.Lock()
        self._seq = 0
        self._prev = GENESIS
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self._resume()

    # ---------------------------------------------------------------- append
    def append(
        self,
        *,
        task_id: str,
        actor: str,
        event: str,
        payload: dict[str, Any] | None = None,
        policy: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            body = build_event(
                trace_id=self.trace_id,
                task_id=task_id,
                actor=actor,
                event=event,
                payload=payload,
                policy=policy,
                execution=execution,
            )
            body["seq"] = self._seq + 1
            body["ts"] = utc_now_iso()
            body["prev_hash"] = self._prev
            body = redact(body)

            digest = hashlib.sha256((self._prev + canonical_json(body)).encode("utf-8")).hexdigest()
            record = dict(body)
            record["hash"] = "sha256:" + digest

            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(canonical_json(record) + "\n")
                fh.flush()

            self._seq = record["seq"]
            self._prev = record["hash"]
            return record

    # ------------------------------------------------------------------ read
    def read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    def verify(self) -> tuple[bool, int | None]:
        """校验整条链。返回 (ok, 第一个坏事件的 seq)。"""
        events = self.read()
        prev = GENESIS
        for ev in events:
            stored_hash = ev.get("hash")
            stored_prev = ev.get("prev_hash")
            if stored_prev != prev:
                return False, ev.get("seq")
            body = {k: v for k, v in ev.items() if k != "hash"}
            expect = "sha256:" + hashlib.sha256((prev + canonical_json(body)).encode("utf-8")).hexdigest()
            if stored_hash != expect:
                return False, ev.get("seq")
            prev = stored_hash
        return True, None

    def export_jsonl(self) -> str:
        if not self.path.exists():
            return ""
        return self.path.read_text(encoding="utf-8")

    # ------------------------------------------------------------------ anchor
    def seal(self, anchor_path: str | Path, *, hmac_key: str | None = None,
             ed25519_private_key: Any | None = None) -> dict[str, Any]:
        """写锚点：事件数量 + 最终哈希（+ 可选 HMAC / Ed25519）。

        锚点与 trace 分开存放，用于检测“末尾被截断/删除”这类链本身仍然自洽的篡改。
        HMAC 只有在密钥不被攻击者拿到时才有意义——这是信任边界，不是绝对保证。
        """
        events = self.read()
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "trace_id": self.trace_id,
            "events": len(events),
            "final_hash": events[-1]["hash"] if events else GENESIS,
            "sealed_at": utc_now_iso(),
        }
        if ed25519_private_key is not None:
            from .signing import sign_anchor

            payload["signature_v2"] = sign_anchor(payload, ed25519_private_key)
        if hmac_key:
            payload["hmac"] = hmac.new(
                hmac_key.encode("utf-8"),
                canonical_json(payload).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        Path(anchor_path).write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return payload

    # ---------------------------------------------------------------- resume
    def _resume(self) -> None:
        events = self.read()
        if not events:
            return
        ok, bad = self.verify()
        if not ok:
            raise TraceError(f"existing trace is corrupted at seq={bad}: {self.path}")
        self._seq = events[-1]["seq"]
        self._prev = events[-1]["hash"]
