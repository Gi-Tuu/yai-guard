#!/usr/bin/env python3
"""Minimal online smoke for a deployed YAI Guard.

Checks Caddy/TLS, /healthz, real-model task completion, SSE and a small
concurrent load. It does not print the demo token and does not bypass auth.

Usage:
    YAI_BASE_URL=https://... YAI_DEMO_TOKEN=... python scripts/online_smoke.py
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse


def request(base: str, token: str, method: str, path: str,
            body: dict | None = None, timeout: float = 60.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(6):
        req = urllib.request.Request(
            base + path, data=data, method=method,
            headers={"Content-Type": "application/json", "X-Demo-Token": token},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 5:
                retry = exc.headers.get("Retry-After", "5")
                time.sleep(min(float(retry), 10.0))
                continue
            raise
    raise RuntimeError("rate limit retries exhausted")


def tls_info(base: str) -> str:
    parsed = urlparse(base)
    if parsed.scheme != "https":
        return "not-https"
    host = parsed.hostname or ""
    port = parsed.port or 443
    ctx = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=10) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            return f"{tls.version()} {tls.cipher()[0]}"


def wait_terminal(base: str, token: str, task_id: str,
                  timeout: float = 120.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = request(base, token, "GET", f"/api/tasks/{task_id}")
        if last.get("status") in ("completed", "failed", "cancelled"):
            return last
        time.sleep(1)
    return last


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("YAI_BASE_URL", ""))
    parser.add_argument("--token", default=os.environ.get("YAI_DEMO_TOKEN", ""))
    parser.add_argument("--tasks", type=int, default=6)
    args = parser.parse_args()
    if not args.base or not args.token:
        parser.error("set --base/--token or YAI_BASE_URL/YAI_DEMO_TOKEN")
    base = args.base.rstrip("/")
    health = request(base, args.token, "GET", "/healthz", timeout=15)
    print(json.dumps({
        "tls": tls_info(base),
        "version": health.get("version"),
        "runtime": (health.get("sandbox") or {}).get("runtime"),
        "task_store": (health.get("task_store") or {}).get("mode"),
        "runtime_mode": health.get("runtime_mode"),
    }, sort_keys=True))

    ids: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.tasks)) as ex:
        futures = [
            ex.submit(request, base, args.token, "POST", "/api/tasks",
                      {"task": f"Run uname -n once and finish. smoke={i}"})
            for i in range(args.tasks)
        ]
        for future in futures:
            ids.append(future.result()["task_id"])

    started = time.monotonic()
    statuses: dict[str, str] = {}
    for task_id in ids:
        final = wait_terminal(base, args.token, task_id)
        statuses[task_id] = final.get("status", "unknown")
    elapsed = round(time.monotonic() - started, 2)

    # One stored-event SSE stream must terminate with the done marker.
    req = urllib.request.Request(
        base + f"/api/tasks/{ids[0]}/events",
        headers={"X-Demo-Token": args.token},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        sse = resp.read().decode()
    print(json.dumps({
        "tasks": len(ids),
        "completed": sum(1 for value in statuses.values() if value == "completed"),
        "elapsed_s": elapsed,
        "sse_done": "event: done" in sse,
    }, sort_keys=True))
    ok = all(value == "completed" for value in statuses.values()) and "event: done" in sse
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
