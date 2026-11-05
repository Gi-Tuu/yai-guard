#!/usr/bin/env python3
"""Compare summary-only acceptance with claim--evidence acceptance on external PRs.

Each case is a real PR from a coding-agent account plus the GitHub check-run
conclusions on its head SHA. ``should_accept`` is true only when at least one
check was observed and none failed/was cancelled. This is a small convenience
sample, not a statistically representative benchmark.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys


def _claim_evidence_accept(case: dict) -> bool:
    evidence = case.get("evidence") or {}
    if evidence.get("kind") != "github_check_runs" or not evidence.get("observed"):
        return False
    conclusions = evidence.get("conclusions") or {}
    return (evidence.get("total", 0) > 0
            and conclusions.get("failure", 0) == 0
            and conclusions.get("cancelled", 0) == 0)


def evaluate(cases: list[dict]) -> dict:
    rows = []
    for case in cases:
        summary_accept = bool((case.get("claim") or "").strip())
        evidence_accept = _claim_evidence_accept(case)
        expected = bool(case["should_accept"])
        rows.append({
            "id": case["id"],
            "source": case["source"],
            "expected": expected,
            "summary_only": summary_accept,
            "claim_evidence": evidence_accept,
            "summary_correct": summary_accept == expected,
            "evidence_correct": evidence_accept == expected,
        })

    def confusion(key: str) -> dict:
        tp = sum(1 for r in rows if r[key] and r["expected"])
        fp = sum(1 for r in rows if r[key] and not r["expected"])
        fn = sum(1 for r in rows if not r[key] and r["expected"])
        tn = sum(1 for r in rows if not r[key] and not r["expected"])
        return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                "reject_recall": round(tn / (tn + fp), 3) if (tn + fp) else None}

    return {
        "cases": len(rows),
        "sources": sorted({r["source"] for r in rows}),
        "accept_expected": sum(1 for r in rows if r["expected"]),
        "reject_expected": sum(1 for r in rows if not r["expected"]),
        "summary_only": confusion("summary_only"),
        "claim_evidence": confusion("claim_evidence"),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="benchmarks/external/cases.json")
    parser.add_argument("--json", action="store_true", help="emit machine-readable result")
    args = parser.parse_args(argv)
    payload = json.loads(pathlib.Path(args.cases).read_text(encoding="utf-8"))
    cases = list(payload.get("cases") or [])
    if not 10 <= len(cases) <= 20:
        print(f"expected 10-20 external cases, got {len(cases)}", file=sys.stderr)
        return 2
    if len({c.get("source") for c in cases}) < 2:
        print("external cases must come from at least two sources", file=sys.stderr)
        return 2
    result = evaluate(cases)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"cases={result['cases']} sources={','.join(result['sources'])}")
        print(f"expected accept={result['accept_expected']} reject={result['reject_expected']}")
        for key in ("summary_only", "claim_evidence"):
            c = result[key]
            print(f"{key}: TP={c['tp']} FP={c['fp']} FN={c['fn']} TN={c['tn']} "
                  f"reject_recall={c['reject_recall']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
