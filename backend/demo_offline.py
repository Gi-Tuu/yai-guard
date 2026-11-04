#!/usr/bin/env python
"""离线演示：Mock 模型 + Fake 沙箱 + 真实策略引擎 + 真实 trace。

不需要 API key、不需要 Docker、不产生任何费用。用于验证：
  Agent 循环 → 策略拦截 → 审批 → 沙箱执行 → 审计链

运行：
    python backend/demo_offline.py
产物：
    artifacts/demo-trace.jsonl
"""
from __future__ import annotations

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from app.agent.loop import run_agent  # noqa: E402
from app.agent.mock_model import MockModel  # noqa: E402
from app.policy import PolicyEngine  # noqa: E402
from app.sandbox.fake_backend import FakeSandbox  # noqa: E402
from app.trace import TraceStore, new_trace_id  # noqa: E402

ROOT = BACKEND.parent
COLORS = {"deny": "\033[31m", "allow": "\033[32m", "require_approval": "\033[33m",
          "exec": "\033[36m", "done": "\033[35m", "reset": "\033[0m"}


def main() -> int:
    trace_id = new_trace_id()
    trace_path = ROOT / "artifacts" / "demo-trace.jsonl"
    if trace_path.exists():
        trace_path.unlink()

    policy = PolicyEngine.from_file()
    trace = TraceStore(trace_path, trace_id)

    def approver(decision, call):
        print(f"  [approval] {decision.rule_id}: {decision.reason} -> APPROVED (demo mode)")
        return True

    def emit(event: dict) -> None:
        kind = event["event"]
        payload = event.get("payload", {})
        color = ""
        if kind == "policy.decision":
            effect = event.get("policy", {}).get("effect")
            color = COLORS.get(effect, "")
            print(f"{color}[{kind}] {effect} {event['policy']['rule_id']} — {event['policy']['reason']}{COLORS['reset']}")
        elif kind == "sandbox.exec":
            print(f"{COLORS['exec']}[{kind}] {event.get('exec', {}).get('argv')}{COLORS['reset']}")
        elif kind == "task.completed":
            print(f"{COLORS['done']}[{kind}] {payload.get('summary', '')}{COLORS['reset']}")
        elif kind == "approval.requested":
            print(f"  [approval] requested: {payload.get('tool')} {payload.get('arguments')}")
        else:
            print(f"[{kind}] {payload}")

    print(f"trace_id = {trace_id}")
    print(f"trace    = {trace_path}")
    print("-" * 72)

    result = run_agent(
        task="创建 main.py 计算 1+1 并运行，然后尝试 rm -rf /，最后总结真实输出",
        model=MockModel(),
        policy=policy,
        sandbox=FakeSandbox(),
        trace=trace,
        task_id="tk_demo",
        approver=approver,
        emit=emit,
    )

    print("-" * 72)
    print(f"status = {result.status}, steps = {result.steps}")
    ok, bad = trace.verify()
    print(f"trace hash-chain verify = {ok}" + ("" if ok else f" (bad seq={bad})"))
    print(f"events = {len(trace.read())}")
    print(f"export = {trace_path}")
    return 0 if ok and result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
