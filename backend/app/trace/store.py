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
import json
import threading
from pathlib import Path
from typing import Any

from .events import build_event, utc_now_iso
from .redact import redact

GENESIS = "sha256:" + "0" * 64


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def verify_file(path: str | Path) -> tuple[bool, int | None]:
    """独立校验一个 JSONL trace 文件（不依赖内存中的 TraceStore 实例）。

    API 的 "校验完整性" 端点用它读取已落盘文件，因此客户端可先下载、再校验。
    返回 (ok, 第一个坏事件的 seq)；文件不存在返回 (False, None)。
    """
    p = Path(path)
    if not p.exists():
        return False, None
    prev = GENESIS
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            return False, None
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
