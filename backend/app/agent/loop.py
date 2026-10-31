# Agent 主循环：单 Agent、可预测、每一步都过策略与审计。
#
# 硬约束（与 05 文档 §12 一致）：
# - 本模块不执行任何命令；执行只经 SandboxBackend
# - 每个 tool_call 必须经过 PolicyEngine.evaluate
# - 每个决策/结果都写进 TraceStore
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..policy import Decision, EvalContext, PolicyEngine, ToolCall
from ..sandbox.interface import SandboxBackend
from ..trace import TraceStore
from .prompts import SYSTEM_PROMPT, USER_TEMPLATE
from .tools import TOOL_SCHEMAS, execute_tool

Approver = Callable[[Decision, ToolCall], bool]
Emitter = Callable[[dict], None]

MAX_FEEDBACK_CHARS = 8192


@dataclass
class RunResult:
    status: str
    summary: str = ""
    steps: int = 0
    events: list[dict] = field(default_factory=list)
    error: str | None = None


def _wrap_output(result: dict[str, Any]) -> str:
    body = json.dumps(result, ensure_ascii=False)
    if len(body) > MAX_FEEDBACK_CHARS:
        body = body[:MAX_FEEDBACK_CHARS] + '...<truncated>"}'
    return f"<sandbox_output>\n{body}\n</sandbox_output>"


def run_agent(
    *,
    task: str,
    model: Any,
    policy: PolicyEngine,
    sandbox: SandboxBackend,
    trace: TraceStore,
    task_id: str,
    approver: Approver | None = None,
    emit: Emitter | None = None,
    max_steps: int | None = None,
    task_timeout_s: int | None = None,
) -> RunResult:
    limits = policy.limits
    max_steps = int(max_steps or limits.get("max_steps", 20))
    timeout_s = int(task_timeout_s or limits.get("task_timeout_s", 900))
    max_output = int(limits.get("max_output_bytes", 8192))
    approver = approver or (lambda _d, _c: False)  # 默认拒绝审批，安全默认

    def emit_event(event: dict) -> None:
        if emit:
            emit(event)

    def record(*, actor: str, event: str, payload: dict | None = None,
               decision: Decision | None = None, execution: dict | None = None) -> dict:
        ev = trace.append(
            task_id=task_id,
            actor=actor,
            event=event,
            payload=payload,
            policy=decision.to_dict() if decision else None,
            execution=execution,
        )
        emit_event(ev)
        return ev

    started = time.monotonic()
    sandbox.create(task_id, _limits_from(policy))
    record(actor="system", event="sandbox.created", payload={
        "runtime": getattr(sandbox, "runtime", "unknown"),
        "limits": limits,
        "host": sandbox.host_fingerprint(),
    })

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(task=task)},
    ]

    step = 0
    try:
        while step < max_steps:
            step += 1
            elapsed = time.monotonic() - started
            if elapsed > timeout_s:
                record(actor="system", event="resource.limit",
                       payload={"kind": "task_timeout", "elapsed_s": round(elapsed, 1)})
                record(actor="system", event="task.failed", payload={"reason": "task_timeout"})
                return RunResult("failed", steps=step, error="task_timeout")

            ctx = EvalContext(step=step, max_steps=max_steps, elapsed_s=elapsed,
                              task_timeout_s=timeout_s, task_id=task_id)
            response = model.complete(messages, tools=TOOL_SCHEMAS)

            event_type = "plan.created" if step == 1 else "agent.step"
            record(actor="agent", event=event_type, payload={
                "step": step,
                "content": response.content,
                "finish_reason": response.finish_reason,
                "usage": response.usage,
                "model": response.model,
            })

            if not response.tool_calls:
                record(actor="agent", event="task.completed",
                       payload={"summary": response.content, "steps": step})
                return RunResult("completed", summary=response.content, steps=step)

            messages.append({
                "role": "assistant",
                "content": response.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                    for tc in response.tool_calls
                ],
            })

            for call in response.tool_calls:
                record(actor="agent", event="tool.requested", payload={
                    "id": call.id, "name": call.name, "arguments": call.arguments,
                })

                decision = policy.evaluate(call, ctx)
                record(actor="policy", event="policy.decision", payload={
                    "tool": call.name, "arguments": call.arguments,
                }, decision=decision)

                if decision.effect.value == "deny":
                    messages.append({"role": "tool", "tool_call_id": call.id,
                                     "content": _wrap_output(decision.for_llm())})
                    continue

                if decision.effect.value == "require_approval":
                    record(actor="policy", event="approval.requested",
                           payload={"tool": call.name, "arguments": call.arguments,
                                    "call_id": call.id, "ttl_s": policy.approval_ttl},
                           decision=decision)
                    approved = bool(approver(decision, call))
                    record(actor="human", event="approval.resolved",
                           payload={"approved": approved, "tool": call.name},
                           decision=decision)
                    if not approved:
                        messages.append({"role": "tool", "tool_call_id": call.id,
                                         "content": _wrap_output({
                                             "ok": False, "decision": "rejected",
                                             "reason": "human rejected this action; choose another approach",
                                         })})
                        continue

                result = execute_tool(call, sandbox, task_id, max_output_bytes=max_output)

                if call.name == "run_command":
                    exec_meta = result.pop("_exec", {}) or {}
                    argv = result.pop("_argv", call.arguments.get("command"))
                    cwd = result.pop("_cwd", "/workspace")
                    record(actor="sandbox", event="sandbox.exec", payload={
                        "tool": call.name,
                    }, execution={**exec_meta, "argv": argv, "cwd": cwd})
                elif call.name == "write_file":
                    record(actor="sandbox", event="file.changed", payload={
                        "path": call.arguments.get("path"),
                        "sha256": result.get("sha256"),
                        "bytes": result.get("bytes"),
                    })
                elif call.name == "finish":
                    record(actor="agent", event="task.completed", payload={
                        "summary": call.arguments.get("summary", ""), "steps": step,
                    })
                    return RunResult("completed", summary=str(call.arguments.get("summary", "")), steps=step)

                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": _wrap_output(result)})

        record(actor="system", event="task.failed", payload={"reason": "max_steps_exceeded", "steps": step})
        return RunResult("failed", steps=step, error="max_steps_exceeded")

    finally:
        sandbox.destroy(task_id)
        record(actor="system", event="sandbox.destroyed", payload={
            "task_id": task_id,
            "reason": "lifecycle_end",
        })


def _limits_from(policy: PolicyEngine):
    from ..sandbox.interface import Limits

    limits = policy.limits
    return Limits(
        cpu=float(limits.get("cpu", 1.0)),
        memory_mb=int(limits.get("memory_mb", 512)),
        pids=int(limits.get("pids", 128)),
        nofile=int(limits.get("nofile", 512)),
        timeout_s=int(limits.get("command_timeout_s", 120)),
        max_output_bytes=int(limits.get("max_output_bytes", 8192)),
    )
