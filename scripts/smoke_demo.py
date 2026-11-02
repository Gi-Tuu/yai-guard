#!/usr/bin/env python
"""演示路径冒烟：真实 API + 真实 Docker 沙箱 + SSE + 审批 + trace 校验。

用途：视频录制前/部署后，一条命令确认"评委看到的那条路径"是通的。
不需要 API key（MOCK_MODEL=1），但需要 Docker daemon 与 yaiguard/sandbox-py:1.0。

运行：
    python scripts/smoke_demo.py
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("SMOKE_PORT", "8012"))
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "smoke-token"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    driver = os.environ.get("SMOKE_SANDBOX_DRIVER", "docker")
    env.update({
        "MOCK_MODEL": "1",
        "SANDBOX_DRIVER": driver,
        "DEMO_TOKEN": TOKEN,
        "RATE_LIMIT_PER_MIN": "0",
        "ARTIFACT_DIR": tempfile.mkdtemp(prefix="yaig-smoke-"),
        "PYTHONUTF8": "1",
    })
    if driver == "remote":
        # 需要先起 sandbox controller：
        #   cd backend && SANDBOX_TOKEN=smoke-sandbox-token \
        #   uvicorn app.controller:app --port 9000
        env["SANDBOX_BASE_URL"] = os.environ.get("SANDBOX_BASE_URL", "http://127.0.0.1:9000")
        env["SANDBOX_TOKEN"] = os.environ.get("SANDBOX_TOKEN", "smoke-sandbox-token")
    return env


def _req(method: str, path: str, body: dict | None = None, *, timeout: float = 30.0,
         token: str | None = TOKEN) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Demo-Token"] = token
    request = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def _wait_health(timeout_s: float = 40.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            _req("GET", "/healthz", token=None, timeout=2)
            return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.3)
    raise RuntimeError("API did not become healthy")


def _stream_and_approve(task_id: str, timeout_s: float = 120.0) -> list[dict]:
    """消费 SSE；遇到 approval.requested 自动批准；返回收到的事件。"""
    events: list[dict] = []
    deadline = time.monotonic() + timeout_s
    request = urllib.request.Request(BASE + f"/api/tasks/{task_id}/events",
                                     headers={"Accept": "text/event-stream",
                                              "X-Demo-Token": TOKEN})
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        for raw in response:
            if time.monotonic() > deadline:
                break
            line = raw.decode("utf-8").strip()
            if line.startswith("event: done"):
                break
            if not line.startswith("data:"):
                continue
            payload = json.loads(line[5:].strip())
            if not payload:
                continue
            events.append(payload)
            if payload.get("event") == "approval.requested":
                call_id = payload.get("payload", {}).get("call_id")
                if call_id:
                    _req("POST", f"/api/tasks/{task_id}/approvals/{call_id}", {"approved": True})
    return events


def main() -> int:
    env = _env()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT / "backend"), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        _wait_health()
        task = _req("POST", "/api/tasks", {
            "task": "创建 main.py 计算 1+1 并运行，然后尝试 rm -rf /，最后总结真实输出"
        })
        task_id = task["task_id"]
        print(f"task_id = {task_id}")

        events = _stream_and_approve(task_id)
        state = _req("GET", f"/api/tasks/{task_id}")
        verify = _req("GET", f"/api/tasks/{task_id}/trace/verify")

        kinds = [e["event"] for e in events]
        policies = [e["policy"] for e in events if e["event"] == "policy.decision"]
        denied = [p["rule_id"] for p in policies if p["effect"] == "deny"]
        approvals = [e for e in events if e["event"] == "approval.resolved"]
        execs = [e.get("exec", {}).get("argv") for e in events if e["event"] == "sandbox.exec"]

        print(f"status      = {state['status']}")
        print(f"events      = {len(events)}")
        print(f"denied      = {denied}")
        print(f"approvals   = {len(approvals)} (approved={approvals[0]['payload']['approved'] if approvals else None})")
        print(f"execs       = {execs}")
        print(f"trace verify= {verify}")

        checks = {
            "completed": state["status"] == "completed",
            "deny rm -rf /": "D-RM-ROOT" in denied,
            "deny curl|sh": "D-CURL-PIPE-SH" in denied,
            "approval happened": bool(approvals) and approvals[0]["payload"]["approved"],
            "real exec": any(argv for argv in execs),
            "trace verified": verify.get("ok") is True,
            "sse events": len(events) >= 20,
        }
        for name, ok in checks.items():
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        return 0 if all(checks.values()) else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        subprocess.run(["docker", "rm", "-f", f"yaig-{task_id}"],
                       capture_output=True) if "task_id" in dir() else None


if __name__ == "__main__":
    raise SystemExit(main())
