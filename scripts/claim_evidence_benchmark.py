#!/usr/bin/env python3
"""Fixed external-control corpus for summary-only vs claim-evidence validation.

This is deliberately not a third-party model benchmark. The corpus lives in
benchmarks/claim_evidence_cases.json and is independent from runtime traces;
it exists to make the detection difference reproducible.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.trace.bundle import _claims_check  # noqa: E402


def _events(case: dict[str, Any], exec_info: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "event": "sandbox.exec",
            "seq": exec_info["seq"],
            "exec": {
                "argv": exec_info["argv"],
                "stdout_sha256": exec_info["stdout_sha256"],
            },
        },
        {
            "event": "task.completed",
            "seq": 3,
            "payload": {
                "summary": case["summary"],
                "claims": case.get("claims"),
                "claims_required": case.get("claims_required") is True,
                "claim_binding": case.get("claim_binding"),
            },
        },
    ]


def evaluate(case: dict[str, Any], exec_info: dict[str, Any]) -> dict[str, Any]:
    result = _claims_check(_events(case, exec_info))
    summary_accept = bool(case.get("summary"))
    claim_accept = result is not False
    return {
        "id": case["id"],
        "expected": case["expected"],
        "summary_only": "accept" if summary_accept else "reject",
        "claim_evidence": "accept" if claim_accept else "reject",
        "claim_result": result,
    }


def run(path: pathlib.Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = [evaluate(case, payload["exec"]) for case in payload["cases"]]
    expected_reject = [row for row in rows if row["expected"] == "reject"]
    expected_accept = [row for row in rows if row["expected"] == "accept"]
    summary_fp = sum(1 for row in expected_reject if row["summary_only"] == "accept")
    claim_fp = sum(1 for row in expected_reject if row["claim_evidence"] == "accept")
    claim_fn = sum(1 for row in expected_accept if row["claim_evidence"] == "reject")
    return {
        "corpus": payload["name"],
        "note": payload["note"],
        "cases": rows,
        "metrics": {
            "total": len(rows),
            "expected_reject": len(expected_reject),
            "expected_accept": len(expected_accept),
            "summary_false_positives": summary_fp,
            "claim_false_positives": claim_fp,
            "claim_false_negatives": claim_fn,
            "summary_reject_recall": round(
                (len(expected_reject) - summary_fp) / max(1, len(expected_reject)), 3
            ),
            "claim_reject_recall": round(
                (len(expected_reject) - claim_fp) / max(1, len(expected_reject)), 3
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(ROOT / "benchmarks" / "claim_evidence_cases.json"))
    args = parser.parse_args()
    result = run(pathlib.Path(args.cases))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["metrics"]["claim_false_negatives"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
