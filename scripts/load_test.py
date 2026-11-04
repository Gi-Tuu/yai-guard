#!/usr/bin/env python
"""Repeatable live load smoke for YAI Guard.

No third-party dependencies. The token is read from the environment by default,
so it never appears in shell history or the repository.

Examples:
    YAI_BASE_URL=https://... YAI_DEMO_TOKEN=... python scripts/load_test.py --tasks 6 --sse 3
    YAI_BASE_URL=https://... YAI_DEMO_TOKEN=... python scripts/load_test.py --tasks 1 --large-output

This is an operator tool, not a CI test; it intentionally creates real tasks.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def request(base: str, token: str, method: str, path: str,
            body: dict | None = None, timeout: float = 30.0) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json", "X-Demo-Token": token}
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def stream(base: str, token: str, task_id: str, timeout: float, report: dict) -> None:
    headers = {"Accept": "text/event-stream", "X-Demo-Token": token}
    req = urllib.request.Request(base + f"/api/tasks/{task_id}/events", headers=headers)
    events = 0
    approvals = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("event: done"):
                    break
                if not line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if not payload:
                    continue
                events += 1
                if payload.get("event") == "approval.requested":
                    call_id = (payload.get("payload") or {}).get("call_id")
                    if call_id:
                        status, _ = request(
                            base, token, "POST",
                            f"/api/tasks/{task_id}/approvals/{call_id}",
                            {"approved": True},
                        )
                        if status == 200:
                            approvals += 1
    except Exception as exc:  # noqa: BLE001 - stream failures are reported, not fatal
        report.setdefault("stream_errors", []).append(f"{task_id}: {type(exc).__name__}: {exc}")
    report.setdefault("streams", []).append(
        {"task_id": task_id, "events": events, "approvals": approvals}
    )


def wait_task(base: str, token: str, task_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, body = request(base, token, "GET", f"/api/tasks/{task_id}", timeout=15)
        if status == 200 and body.get("status") in ("completed", "failed", "cancelled"):
            return body
        time.sleep(1.0)
    return {"task_id": task_id, "status": "timeout"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Live load/SSE smoke for YAI Guard")
    parser.add_argument("--base", default=os.environ.get("YAI_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--token", default=os.environ.get("YAI_DEMO_TOKEN", ""))
    parser.add_argument("--tasks", type=int, default=6)
    parser.add_argument("--sse", type=int, default=0, help="how many tasks also open an SSE stream")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--large-output", action="store_true")
    parser.add_argument("--prompt", default="Run uname -n once and finish.")
    args = parser.parse_args()
    if not args.token:
        parser.error("--token or YAI_DEMO_TOKEN is required")

    prompt = (
        "Run exactly: seq 1 200000. Then finish and report exit code and whether output was truncated."
        if args.large_output else args.prompt
    )
    started = time.monotonic()
    submitted: list[dict] = []
    http_errors: list[dict] = []
    for _ in range(max(1, args.tasks)):
        status, body = request(args.base, args.token, "POST", "/api/tasks",
                               {"task": prompt}, timeout=30)
        if status == 200:
            body["_started"] = time.monotonic()
            submitted.append(body)
        else:
            http_errors.append({"status": status, "body": body})

    report: dict = {"submitted": len(submitted), "http_errors": http_errors}
    threads = []
    for index, item in enumerate(submitted[: max(0, args.sse)]):
        thread = threading.Thread(
            target=stream,
            args=(args.base, args.token, item["task_id"], args.timeout, report),
            daemon=True,
            name=f"sse-{index}",
        )
        thread.start()
        threads.append(thread)

    results = [wait_task(args.base, args.token, item["task_id"], args.timeout)
               for item in submitted]
    for thread in threads:
        thread.join(timeout=5)

    finished_at = time.monotonic()
    latencies = [finished_at - item["_started"] for item in submitted]
    statuses = {}
    for item in results:
        statuses[item.get("status", "unknown")] = statuses.get(item.get("status", "unknown"), 0) + 1
    report.update({
        "statuses": statuses,
        "wall_s": round(finished_at - started, 2),
        "latency_s": {
            "p50": round(statistics.median(latencies), 2) if latencies else None,
            "max": round(max(latencies), 2) if latencies else None,
        },
        "tasks": [{"task_id": r.get("task_id"), "status": r.get("status")} for r in results],
        "streams": report.get("streams", []),
        "stream_errors": report.get("stream_errors", []),
    })
    print(json.dumps(report, indent=2, ensure_ascii=False))
    failed = statuses.get("failed", 0) + statuses.get("timeout", 0) + len(http_errors)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
