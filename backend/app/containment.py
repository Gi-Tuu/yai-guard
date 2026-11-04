"""Containment Lab：直接通过沙箱后端执行 5 个高信号探针。

这不是策略测试，而是容器边界测试：探针不经过 policy/LLM，直接调用 sandbox.exec。
结果只报告真实 exit code / 关键错误片段，不把“配置声明”当成证据。
wall_clock 探针放在最后：超时会杀掉容器，之后仍执行 destroy 确认清理。
"""
from __future__ import annotations

from typing import Any

from .sandbox.interface import ExecResult, Limits

PROBES: list[dict[str, Any]] = [
    {
        "name": "read_only_root",
        "argv": "python3 -c \"open('/etc/hostname','a').write('x')\"",
        "timeout_s": 15,
        "expect_nonzero": True,
        "expect_any": ["Read-only file system", "Permission denied"],
        "description": "write to /etc must be refused by the read-only rootfs",
    },
    {
        "name": "network_none",
        "argv": "python3 -c \"import socket; socket.create_connection(('1.1.1.1',443),3)\"",
        "timeout_s": 15,
        "expect_nonzero": True,
        "expect_any": ["Network is unreachable", "Temporary failure in name resolution",
                       "Name or service not known", "timed out"],
        "description": "outbound TCP must be unreachable",
    },
    {
        "name": "no_docker_socket",
        "argv": "python3 -c \"import os; print('SOCKET=' + str(os.path.exists('/var/run/docker.sock')))\"",
        "timeout_s": 15,
        "expect_nonzero": False,
        "expect_stdout_contains": "SOCKET=False",
        "description": "sandbox must not see the host docker socket",
    },
    {
        "name": "no_secrets",
        "argv": "env",
        "timeout_s": 15,
        "expect_nonzero": False,
        "forbid_any": ["VULTR_INFERENCE_API_KEY", "SANDBOX_TOKEN", "DEMO_TOKEN",
                       "TRACE_HMAC_KEY", "BEGIN OPENSSH PRIVATE KEY"],
        "description": "sandbox environment must not contain app/inference/sandbox secrets",
    },
    {
        "name": "wall_clock",
        "argv": "python3 -c \"import time; time.sleep(30)\"",
        "timeout_s": 3,
        "expect_timed_out": True,
        "description": "a runaway loop/process must be killed at the wall-clock cap",
    },
]


def _check(probe: dict[str, Any], result: ExecResult) -> tuple[bool, str]:
    text = (result.stdout or "") + "\n" + (result.stderr or "")
    if probe.get("expect_timed_out") is True:
        return result.timed_out, "timed_out" if result.timed_out else "expected timeout"
    if probe.get("expect_nonzero") is True and result.exit_code == 0:
        return False, "expected non-zero exit code"
    if probe.get("expect_nonzero") is False and result.exit_code != 0:
        return False, "expected zero exit code"
    for needle in probe.get("expect_any", []):
        if needle.lower() in text.lower():
            return True, needle
    for needle in probe.get("forbid_any", []):
        if needle in text:
            return False, "forbidden marker present: " + needle
    if probe.get("expect_stdout_contains"):
        return (probe["expect_stdout_contains"] in result.stdout,
                "stdout contains expected marker")
    if probe.get("expect_any"):
        return False, "expected error marker not found"
    return True, "ok"


def run_containment(sandbox: Any, task_id: str) -> dict[str, Any]:
    limits = Limits(cpu=1.0, memory_mb=512, pids=128, timeout_s=60)
    sandbox.create(task_id, limits)
    probes: list[dict[str, Any]] = []
    try:
        for probe in PROBES:
            result = sandbox.exec(task_id, probe["argv"], timeout_s=probe["timeout_s"])
            passed, evidence = _check(probe, result)
            item = {
                "name": probe["name"],
                "description": probe["description"],
                "argv": probe["argv"],
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "killed_reason": result.killed_reason,
                "passed": passed,
                "evidence": evidence,
            }
            if probe["name"] != "no_secrets":
                item["stdout"] = (result.stdout or "")[:300]
                item["stderr"] = (result.stderr or "")[:300]
            probes.append(item)
    finally:
        cleanup = sandbox.destroy(task_id)

    return {
        "ok": bool(probes) and all(p["passed"] for p in probes) and cleanup.get("verified") is True,
        "task_id": task_id,
        "limits": limits.to_dict(),
        "probes": probes,
        "cleanup": cleanup,
        "summary": f"{sum(1 for p in probes if p['passed'])}/{len(probes)} contained",
    }
