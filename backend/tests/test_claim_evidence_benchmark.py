"""Validate the fixed external-control corpus and its reported metrics."""

from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from claim_evidence_benchmark import run  # noqa: E402


class ClaimEvidenceBenchmarkTest(unittest.TestCase):
    def test_fixed_corpus_metrics_and_labels(self) -> None:
        result = run(ROOT / "benchmarks" / "claim_evidence_cases.json")
        metrics = result["metrics"]
        self.assertEqual(metrics["total"], 12)
        self.assertEqual(metrics["expected_reject"], 8)
        self.assertEqual(metrics["expected_accept"], 4)
        self.assertEqual(metrics["summary_false_positives"], 8)
        self.assertEqual(metrics["claim_false_positives"], 0)
        self.assertEqual(metrics["claim_false_negatives"], 0)
        self.assertEqual(metrics["claim_reject_recall"], 1.0)

        for row in result["cases"]:
            self.assertEqual(row["claim_evidence"], row["expected"], row)
