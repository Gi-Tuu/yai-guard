#!/usr/bin/env python
"""Prune old terminal tasks, approvals, traces and anchors.

Dry-run by default. This is deliberately an operator tool: evidence should not
disappear silently. Run it from the app host with the service stopped or after a
backup.

    python scripts/prune_state.py --artifacts artifacts --older-than 30
    python scripts/prune_state.py --artifacts artifacts --older-than 30 --apply
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prune old terminal task state")
    parser.add_argument("--artifacts", default="artifacts")
    parser.add_argument("--older-than", type=float, default=30.0, dest="days",
                        help="retention in days (default 30)")
    parser.add_argument("--apply", action="store_true",
                        help="actually delete; default is dry-run")
    args = parser.parse_args(argv)
    if args.days <= 0:
        parser.error("--older-than must be positive")

    root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "backend"))
    from app.tasks.service import TaskService

    artifacts = pathlib.Path(args.artifacts).resolve()
    service = TaskService.from_path(artifacts / "tasks.db")
    if not service.enabled:
        print(json.dumps({"ok": False, "error": "task store unavailable"}))
        return 1
    cutoff = time.time() - args.days * 86400
    try:
        records = service.prune_terminal_before(cutoff) if args.apply \
            else service.list_terminal_before(cutoff)
        traces = []
        if args.apply:
            for record in records:
                for suffix in (".jsonl", ".anchor.json"):
                    path = artifacts / "traces" / f"{record.trace_id}{suffix}"
                    if path.exists():
                        path.unlink()
                        traces.append(str(path))
        print(json.dumps({
            "ok": True,
            "applied": args.apply,
            "cutoff_days": args.days,
            "tasks": [{"id": r.id, "trace_id": r.trace_id, "status": r.status.value}
                      for r in records],
            "traces": traces,
        }, indent=2, ensure_ascii=False))
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
