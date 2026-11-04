# Agent 主循环：单 Agent、可预测、每一步都过策略与审计。
#
# 硬约束（与 05 文档 §12 一致）：
# - 本模块不执行任何命令；执行只经 SandboxBackend
# - 每个 tool_call 必须经过 PolicyEngine.evaluate
# - 每个决策/结果都写进 TraceStore
from __future__ import annotations

import inspect
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


def _validate_claims(
    claims: Any, exec_seqs: list[int], *, required: bool
) -> tuple[list[dict[str, Any]], str | None]:
    """每条 claim 必须引用真实 sandbox.exec 事件的 seq；空/无效引用一律拒绝。"""
    if claims is None:
        if required:
            return [], "claims are required when a command was executed"
        return [], None
    if not isinstance(claims, list):
        return [], "claims must be an array"
    if required and not claims:
        return [], "claims must not be empty when a command was executed"
    validated: list[dict[str, Any]] = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            return [], f"claim[{index}] must be an object"
        text = str(claim.get("text") or "").strip()
        seq = claim.get("evidence_seq")
        if not text:
            return [], f"claim[{index}].text must be non-empty"
        if not isinstance(seq, int) or seq not in exec_seqs:
            return [], (f"claim[{index}].evidence_seq={seq!r} is not a real sandbox.exec seq; "
                        f"available={exec_seqs}")
        validated.append({"text": text, "evidence_seq": seq})
    return validated, None


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
    require_claims: bool = False,
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
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(task=task)},
    ]

    step = 0
    exec_seqs: list[int] = []
    run_result: RunResult | None = None
    try:
        sandbox.create(task_id, _limits_from(policy))
        info = sandbox.sandbox_info() if callable(getattr(sandbox, "sandbox_info", None)) else {}
        record(actor="system", event="sandbox.created", payload={
            "runtime": getattr(sandbox, "runtime", "unknown"),
            "limits": limits,
            "sandbox": info,
            "host": sandbox.host_fingerprint(),
            "host_fingerprint": _host_snapshot(sandbox),
        })
        while step < max_steps:
            step += 1
            elapsed = time.monotonic() - started
            if elapsed > timeout_s:
                record(actor="system", event="resource.limit",
                       payload={"kind": "task_timeout", "elapsed_s": round(elapsed, 1)})
                record(actor="system", event="task.failed", payload={"reason": "task_timeout"})
                run_result = RunResult("failed", steps=step, error="task_timeout")
                return run_result

            ctx = EvalContext(step=step, max_steps=max_steps, elapsed_s=elapsed,
                              task_timeout_s=timeout_s, task_id=task_id)
            remaining_model_s = max(1, int(timeout_s - elapsed))
            if "deadline_s" in inspect.signature(model.complete).parameters:
                response = model.complete(
                    messages, tools=TOOL_SCHEMAS, deadline_s=remaining_model_s
                )
            else:
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
                # A plain assistant answer is not a verifiable finish when commands
                # ran. Ask the model to call finish with evidence, instead of
                # silently accepting an uncited summary.
                if require_claims and exec_seqs:
                    messages.append({"role": "assistant", "content": response.content or ""})
                    messages.append({
                        "role": "user",
                        "content": (
                            "You executed commands, so the task is not finished yet. "
                            "Call finish with a summary and claims; every claim must cite "
                            "an evidence_seq returned by run_command. Available evidence_seq: "
                            + ", ".join(str(seq) for seq in exec_seqs)
                        ),
                    })
                    continue
                run_result = RunResult(
                    "completed",
                    summary=response.content,
                    steps=step,
                )
                record(actor="agent", event="task.completed",
                       payload={"summary": response.content, "steps": step,
                                "claims": [], "claims_verified": False,
                                "claims_required": bool(require_claims and exec_seqs)})
                return run_result

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

                host_before = _host_snapshot(sandbox) if call.name == "run_command" else None
                remaining_s = max(1, int(timeout_s - (time.monotonic() - started)))
                result = execute_tool(
                    call,
                    sandbox,
                    task_id,
                    max_output_bytes=max_output,
                    max_command_timeout=min(
                        int(limits.get("command_timeout_s", 120)), remaining_s
                    ),
                )
                host_after = _host_snapshot(sandbox) if call.name == "run_command" else None

                if call.name == "run_command":
                    exec_meta = result.pop("_exec", {}) or {}
                    argv = result.pop("_argv", call.arguments.get("command"))
                    cwd = result.pop("_cwd", "/workspace")
                    exec_event = record(actor="sandbox", event="sandbox.exec", payload={
                        "tool": call.name,
                    }, execution={**exec_meta, "argv": argv, "cwd": cwd,
                                  "host_before": host_before, "host_after": host_after})
                    exec_seqs.append(exec_event["seq"])
                    result["evidence_seq"] = exec_event["seq"]
                    killed = exec_meta.get("killed_reason")
                    if killed:
                        record(actor="sandbox", event="resource.limit", payload={
                            "kind": killed,
                            "exit_code": exec_meta.get("exit_code"),
                            "pids_limit": limits.get("pids"),
                            "memory_mb": limits.get("memory_mb"),
                            "oom": killed == "oom",
                            "timed_out": bool(exec_meta.get("timed_out")),
                        })
                elif call.name == "write_file":
                    record(actor="sandbox", event="file.changed", payload={
                        "path": call.arguments.get("path"),
                        "sha256": result.get("sha256"),
                        "bytes": result.get("bytes"),
                    })
                elif call.name == "finish":
                    claims, claim_error = _validate_claims(
                        call.arguments.get("claims"),
                        exec_seqs,
                        required=require_claims and bool(exec_seqs),
                    )
                    if claim_error:
                        messages.append({"role": "tool", "tool_call_id": call.id,
                                         "content": _wrap_output({
                                             "ok": False,
                                             "error": "finish rejected: " + claim_error,
                                             "available_evidence_seqs": exec_seqs,
                                             "hint": "call finish again with claims citing a real evidence_seq",
                                         })})
                        continue
                    record(actor="agent", event="task.completed", payload={
                        "summary": call.arguments.get("summary", ""),
                        "steps": step,
                        "claims": claims,
                        "claims_verified": bool(claims),
                        "claims_required": bool(require_claims and exec_seqs),
                    })
                    run_result = RunResult(
                        "completed",
                        summary=str(call.arguments.get("summary", "")),
                        steps=step,
                    )
                    return run_result

                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": _wrap_output(result)})

        record(actor="system", event="task.failed", payload={"reason": "max_steps_exceeded", "steps": step})
        run_result = RunResult("failed", steps=step, error="max_steps_exceeded")
        return run_result

    except Exception as exc:
        # 先记失败，再进 finally 记销毁：保证 trace 的最后一条是销毁确认。
        record(actor="system", event="task.failed",
               payload={"error": f"{type(exc).__name__}: {exc}"})
        raise

    finally:
        # 销毁必须被验证：成功写 confirmed，失败/无法确认写 unverified，绝不静默吞掉。
        try:
            cleanup = sandbox.destroy(task_id) or {}
        except Exception as exc:  # noqa: BLE001 - 清理失败要记录，不能掩盖任务结果
            cleanup = {"ok": False, "verified": False,
                       "backend": type(sandbox).__name__,
                       "error": f"{type(exc).__name__}: {exc}"}
        event = "sandbox.destroy.confirmed" if cleanup.get("verified") is True \
            else "sandbox.destroy.unverified"
        record(actor="system", event=event, payload={
            "task_id": task_id,
            "reason": "lifecycle_end",
            "cleanup": cleanup,
        })
        if run_result is not None and run_result.status == "completed" \
                and cleanup.get("verified") is not True:
            run_result.status = "failed"
            run_result.error = "cleanup_unverified"


def _host_snapshot(sandbox: SandboxBackend) -> dict:
    """沙箱后端的宿主指纹；任何异常都返回 {}，绝不因为取证失败而中断任务。"""
    getter = getattr(sandbox, "host_snapshot", None)
    if not callable(getter):
        return {}
    try:
        return getter() or {}
    except Exception:  # noqa: BLE001 - 取证是旁路，不能影响主流程
        return {}


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
        max_total_output_bytes=int(limits.get("max_total_output_bytes", 1_048_576)),
    )
