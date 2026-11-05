# Append-only 审计存储 + 哈希链。
#
# 每条事件：
#   hash = sha256(prev_hash + canonical_json(event 去掉 hash))
# 任何一条被改动/删除，verify() 都会在该条失败。
#
# 注意：哈希链只能证明「这份日志在生成后未被篡改」，不能证明「日志本身完整记录了
# 所有操作」——后者依靠 Agent 只通过 TraceStore 写审计这一代码约束。
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterator

from .events import build_event, utc_now_iso
from .redact import redact

GENESIS = "sha256:" + "0" * 64


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@contextlib.contextmanager
def _locked_trace_file(path: Path) -> Iterator[Any]:
    """Serialize appends across process-level TraceStore instances.

    A lease can expire while the original worker is merely paused. The recovery
    leader must not be able to interleave a second writer and corrupt seq/hash.
    Linux uses flock on the trace file; Windows uses a separate lock file and
    msvcrt byte locking.
    """
    handle = open(path, "a+", encoding="utf-8")
    lock_handle = None
    try:
        if os.name == "nt":
            import msvcrt

            lock_path = Path(tempfile.gettempdir()) / (
                "yaig-trace-" + hashlib.sha256(
                    str(path.resolve()).encode("utf-8")
                ).hexdigest() + ".lock"
            )
            lock_handle = open(lock_path, "a+b")
            if lock_handle.seek(0, os.SEEK_END) == 0:
                lock_handle.write(b"\0")
                lock_handle.flush()
            lock_handle.seek(0)
            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)
            yield handle
            return
        try:
            import fcntl
        except ImportError:
            yield handle
            return
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield handle
    finally:
        if lock_handle is not None:
            try:
                import msvcrt

                lock_handle.seek(0)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:  # noqa: BLE001 - close releases the lock
                pass
            lock_handle.close()
        else:
            try:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:  # noqa: BLE001 - close releases the lock
                pass
        handle.close()


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


def _cleanup_state(events: list[dict[str, Any]], last: str | None) -> bool | None:
    if last == "sandbox.destroy.unverified":
        return False
    if last == "sandbox.destroy.confirmed":
        payload = events[-1].get("payload") or {}
        cleanup = payload.get("cleanup") or {}
        # Event name alone is not proof; the payload must say JSON true.
        return cleanup.get("verified") is True
    # Legacy event only proves that a destroy was attempted. Strict
    # verification must not turn it into a confirmed cleanup.
    return None


def _completeness_state(task_status: str | None, kinds: list[str | None],
                        last: str | None, cleanup_ok: bool | None) -> bool | None:
    if task_status == "completed":
        return bool("task.completed" in kinds and cleanup_ok is True)
    if task_status == "failed":
        return bool("task.failed" in kinds
                    or ("task.completed" in kinds
                        and last == "sandbox.destroy.unverified"))
    if task_status == "cancelled":
        return bool("task.cancelled" in kinds
                    and ("sandbox.created" not in kinds or cleanup_ok is True))
    if task_status == "interrupted":
        # Recovery proves the interruption; a confirmed cleanup is nice but not
        # required because the crashed process never got to write it.
        return "task.interrupted" in kinds
    return None


def _anchor_state(anchor_path: str | Path | None, event_count: int,
                  final_hash: str | None, hmac_key: str | None,
                  public_key: Any | None) -> dict[str, Any]:
    state: dict[str, Any] = {
        "anchor_ok": None, "authentic": None, "signature_alg": None,
        "ed25519_ok": None, "reason": None,
    }
    anchor_present = bool(anchor_path) and Path(anchor_path).exists()
    if anchor_path is not None and not anchor_present:
        state["anchor_ok"] = False
        state["reason"] = "anchor_missing"
        return state
    if not anchor_present:
        return state
    try:
        anchor = json.loads(Path(anchor_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state["anchor_ok"] = False
        state["reason"] = "anchor_malformed"
        return state
    state["anchor_ok"] = (
        anchor.get("events") == event_count
        and anchor.get("final_hash") == final_hash
    )
    if not state["anchor_ok"]:
        state["reason"] = "anchor_mismatch"
    if hmac_key:
        supplied = anchor.get("hmac")
        body = {k: v for k, v in anchor.items() if k != "hmac"}
        expect = hmac.new(hmac_key.encode("utf-8"),
                          canonical_json(body).encode("utf-8"),
                          hashlib.sha256).hexdigest()
        if not supplied:
            state["authentic"] = False
            state["reason"] = state["reason"] or "hmac_missing"
        else:
            state["authentic"] = hmac.compare_digest(supplied, expect)
            if not state["authentic"]:
                state["reason"] = "hmac_mismatch"
    if public_key is not None:
        from .signing import verify_anchor_signature

        state["signature_alg"] = "ed25519"
        state["ed25519_ok"] = verify_anchor_signature(anchor, public_key)
        if not state["ed25519_ok"]:
            state["authentic"] = False
            state["reason"] = state["reason"] or "ed25519_mismatch"
    return state


def _final_status(result: dict[str, Any], *, strict: bool, terminal: bool,
                  task_status: str | None, kinds: list[str | None],
                  hmac_key: str | None, public_key: Any | None) -> str:
    if not result["chain_ok"] or not result["sequence_ok"]:
        return "FAILED"
    if result["completeness_ok"] is False or result["cleanup_ok"] is False:
        return "INCOMPLETE"
    if strict and terminal:
        if result["anchor_ok"] is not True:
            return "INCOMPLETE" if result["anchor_ok"] is not False else "FAILED"
        if (result["cleanup_ok"] is not True and task_status != "interrupted"
                and not (task_status in ("failed", "cancelled")
                         and "sandbox.created" not in kinds)):
            return "INCOMPLETE"
        if hmac_key and result["authentic"] is not True:
            return "FAILED"
        if public_key is not None and result["ed25519_ok"] is not True:
            return "FAILED"
        return "VERIFIED"
    if result["anchor_ok"] is False or result["authentic"] is False:
        return "FAILED"
    return "VERIFIED"


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
    queued/running/waiting_approval 返回 PENDING；无签名时终态内容校验返回
    CONTENT_VERIFIED，配置并验证签名后才返回 VERIFIED。
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
    if task_status in ("queued", "running", "waiting_approval"):
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
    result["cleanup_ok"] = _cleanup_state(events, last)
    result["completeness_ok"] = _completeness_state(
        task_status, kinds, last, result["cleanup_ok"])

    result.update(_anchor_state(anchor_path, len(events), result["final_hash"],
                                hmac_key, public_key))
    result["status"] = _final_status(
        result, strict=strict, terminal=terminal, task_status=task_status,
        kinds=kinds, hmac_key=hmac_key, public_key=public_key,
    )

    # VERIFIED means content plus a verified signature. Unsigned traces are
    # content-consistent but must not claim signature provenance.
    if result["status"] == "VERIFIED" and hmac_key is None and public_key is None:
        result["status"] = "CONTENT_VERIFIED"
    result["ok"] = result["status"] in ("VERIFIED", "CONTENT_VERIFIED")
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
    def _assert_current(self) -> None:
        """Refuse to append/seal from a stale instance after another writer."""
        events = self.read()
        if not events:
            if self._seq != 0 or self._prev != GENESIS:
                raise TraceError(
                    f"trace was truncated by another writer: {self.path}"
                )
            return
        last = events[-1]
        if last.get("seq") != self._seq or last.get("hash") != self._prev:
            raise TraceError(
                f"trace changed by another writer at seq={last.get('seq')}: {self.path}"
            )

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
            with _locked_trace_file(self.path) as handle:
                self._assert_current()
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

                digest = hashlib.sha256(
                    (self._prev + canonical_json(body)).encode("utf-8")
                ).hexdigest()
                record = dict(body)
                record["hash"] = "sha256:" + digest

                handle.write(canonical_json(record) + "\n")
                handle.flush()

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
        with self._lock, _locked_trace_file(self.path):
            self._assert_current()
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
            target = Path(anchor_path)
            tmp = target.with_name(target.name + ".tmp")
            try:
                tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
                os.replace(tmp, target)
            finally:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
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

