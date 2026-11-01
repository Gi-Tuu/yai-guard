#!/usr/bin/env python
"""真实 Docker 端到端演示：真容器 + 真策略 + 真 trace + Mock 模型。

与 demo_offline.py 的区别：执行发生在真实的加固容器里（不是 FakeSandbox）。
不需要 API key；需要本机 Docker 守护进程与 `yaiguard/sandbox-py:1.0` 镜像。

运行：
    python backend/demo_docker.py
产物：
    artifacts/e2e-docker/trace.jsonl
"""
from __future__ import annotations

import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

from app.agent.loop import run_agent  # noqa: E402
from app.agent.mock_model import MockModel  # noqa: E402
from app.policy import PolicyEngine  # noqa: E402
from app.sandbox.docker_backend import DockerSandbox  # noqa: E402
from app.trace import TraceStore, new_trace_id  # noqa: E402

ROOT = BACKEND.parent


def main() -> int:
    trace_id = new_trace_id()
    trace_path = ROOT / "artifacts" / "e2e-docker" / "trace.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    if trace_path.exists():
        trace_path.unlink()

    policy = PolicyEngine.from_file()
    trace = TraceStore(trace_path, trace_id)
    sandbox = DockerSandbox()

    def emit(ev: dict) -> None:
        kind = ev["event"]
        payload = ev.get("payload", {})
        if kind == "policy.decision":
            pol = ev.get("policy", {})
            print(f"[{kind}] {pol.get('effect')} {pol.get('rule_id')} — {pol.get('reason')}")
        elif kind == "sandbox.exec":
            print(f"[{kind}] {ev.get('exec', {}).get('argv')} -> exit={ev.get('exec', {}).get('exit_code')}")
        elif kind in ("task.completed", "task.failed", "sandbox.created", "sandbox.destroyed"):
            print(f"[{kind}] {payload}")
        elif kind == "approval.requested":
            print(f"[approval] {payload.get('tool')} {payload.get('arguments')}")

    print(f"trace_id = {trace_id}")
    print(f"image    = {DockerSandbox.__module__} / yaiguard/sandbox-py:1.0")
    print("-" * 72)

    try:
        result = run_agent(
            task="创建 main.py 计算 1+1 并运行，然后尝试 rm -rf /，最后总结真实输出",
            model=MockModel(),
            policy=policy,
            sandbox=sandbox,
            trace=trace,
            task_id="tk_e2e_docker",
            approver=lambda decision, call: True,
            emit=emit,
        )
    finally:
        sandbox.destroy("tk_e2e_docker")

    print("-" * 72)
    print(f"status = {result.status}, steps = {result.steps}")
    ok, bad = trace.verify()
    events = trace.read()
    print(f"hash-chain verify = {ok}" + ("" if ok else f" (bad seq={bad})"))
    print(f"events = {len(events)}")
    for ev in events:
        if ev["event"] == "sandbox.exec":
            print(f"  exec: argv={ev.get('exec', {}).get('argv')!r} exit={ev.get('exec', {}).get('exit_code')}")
    return 0 if ok and result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
