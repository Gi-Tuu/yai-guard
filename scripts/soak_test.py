#!/usr/bin/env python3
"""Small online soak: submit tasks, wait, report latency percentiles."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request


def _request(base: str, token: str, method: str, path: str,
             body: dict | None = None, timeout: float = 120.0) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json", "X-Demo-Token": token},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * pct) - 1))
    return round(ordered[index], 3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=os.environ.get("YAI_BASE_URL", ""))
    parser.add_argument("--token", default=os.environ.get("YAI_DEMO_TOKEN", ""))
    parser.add_argument("--tasks", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--retries", type=int, default=8)
    args = parser.parse_args()
    if not args.base or not args.token:
        parser.error("set --base/--token or YAI_BASE_URL/YAI_DEMO_TOKEN")
    base = args.base.rstrip("/")
    started = time.monotonic()

    def create(index: int) -> tuple[str, float]:
        for attempt in range(args.retries):
            try:
                response = _request(base, args.token, "POST", "/api/tasks",
                                    {"task": f"Run uname -n once and finish. soak={index}"})
                return response["task_id"], time.monotonic()
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt + 1 < args.retries:
                    time.sleep(min(float(exc.headers.get("Retry-After", "5")), 10.0))
                    continue
                raise
        raise RuntimeError("submission retries exhausted")

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        created = list(pool.map(create, range(args.tasks)))
    latencies: list[float] = []
    failures = 0
    for task_id, submitted_at in created:
        while True:
            record = _request(base, args.token, "GET", f"/api/tasks/{task_id}")
            if record.get("status") in ("completed", "failed", "cancelled"):
                latencies.append(time.monotonic() - submitted_at)
                if record["status"] != "completed":
                    failures += 1
                break
            time.sleep(0.5)
    result = {
        "tasks": args.tasks,
        "completed": args.tasks - failures,
        "failed": failures,
        "wall_s": round(time.monotonic() - started, 2),
        "p50_s": _percentile(latencies, 0.50),
        "p95_s": _percentile(latencies, 0.95),
        "p99_s": _percentile(latencies, 0.99),
    }
    print(json.dumps(result, sort_keys=True))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
