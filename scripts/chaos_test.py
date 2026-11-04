#!/usr/bin/env python
"""Operator chaos smoke: restart during a task, then tunnel outage and recovery.

Commands are supplied by the operator, because host access differs per deployment:

    YAI_BASE_URL=... YAI_DEMO_TOKEN=... python scripts/chaos_test.py \
      --restart-cmd "ssh app 'systemctl restart yai-guard'" \
      --tunnel-stop-cmd "ssh app 'systemctl stop yai-tunnel'" \
      --tunnel-start-cmd "ssh app 'systemctl start yai-tunnel'"

No credentials or hostnames are stored in the script.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request


def request(base: str, token: str, method: str, path: str,
            body: dict | None = None, timeout: float = 30.0) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json", "X-Demo-Token": token}
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def wait_status(base: str, token: str, task_id: str, statuses: set[str],
                timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        _, last = request(base, token, "GET", f"/api/tasks/{task_id}")
        if last.get("status") in statuses:
            return last
        time.sleep(1)
    return last


def run(label: str, command: str | None, report: list[dict]) -> None:
    if not command:
        report.append({"step": label, "skipped": True})
        return
    started = time.monotonic()
    proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=120)
    report.append({
        "step": label,
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "duration_s": round(time.monotonic() - started, 2),
        "stderr": (proc.stderr or "")[-500:],
    })


def main() -> int:
    parser = argparse.ArgumentParser(description="Chaos smoke for YAI Guard")
    parser.add_argument("--base", default=os.environ.get("YAI_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.environ.get("YAI_DEMO_TOKEN", ""))
    parser.add_argument("--restart-cmd", default=None)
    parser.add_argument("--tunnel-stop-cmd", default=None)
    parser.add_argument("--tunnel-start-cmd", default=None)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    if not args.token:
        parser.error("--token or YAI_DEMO_TOKEN is required")

    steps: list[dict] = []
    result: dict = {"ok": True, "steps": steps}
    _, health = request(args.base, args.token, "GET", "/healthz")
    result["health_before"] = health.get("ok")

    _, created = request(
        args.base, args.token, "POST", "/api/tasks",
        {"task": "Create /workspace/tmp/chaos.txt then run exactly: rm -rf /workspace/tmp. Report."},
    )
    task_id = created.get("task_id")
    if not task_id:
        print(json.dumps({"ok": False, "error": "task creation failed", "body": created}))
        return 1
    before = wait_status(args.base, args.token, task_id, {"waiting_approval"}, args.timeout)
    steps.append({"step": "approval_wait", "status": before.get("status")})

    run("restart_app", args.restart_cmd, steps)
    if args.restart_cmd:
        after = wait_status(args.base, args.token, task_id,
                            {"interrupted", "failed", "cancelled", "completed"}, args.timeout)
        steps.append({"step": "after_restart", "status": after.get("status")})
        if after.get("status") != "interrupted":
            result["ok"] = False

    if args.tunnel_stop_cmd:
        run("tunnel_stop", args.tunnel_stop_cmd, steps)
        time.sleep(2)
        code, _ = request(args.base, args.token, "GET", "/healthz")
        steps.append({"step": "health_tunnel_down", "http": code})
        if code == 200:
            result["ok"] = False
        _, created2 = request(args.base, args.token, "POST", "/api/tasks",
                              {"task": "Run uname -n once and finish."})
        task2 = created2.get("task_id")
        if task2:
            failed = wait_status(args.base, args.token, task2,
                                 {"failed", "completed", "cancelled"}, args.timeout)
            steps.append({"step": "task_tunnel_down", "status": failed.get("status"),
                          "error": failed.get("error")})
            if failed.get("status") != "failed":
                result["ok"] = False
        run("tunnel_start", args.tunnel_start_cmd, steps)
        time.sleep(3)
        code, _ = request(args.base, args.token, "GET", "/healthz")
        steps.append({"step": "health_tunnel_up", "http": code})
        if code != 200:
            result["ok"] = False

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
