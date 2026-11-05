"""Validate the checked-in external sample without network access."""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


class ExternalBenchmarkTest(unittest.TestCase):
    def _module(self):
        spec = importlib.util.spec_from_file_location(
            "external_benchmark", ROOT / "scripts" / "claim_evidence_external.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_sample_is_multi_source_and_evidence_separates_failures(self) -> None:
        payload = json.loads(
            (ROOT / "benchmarks" / "external" / "cases.json").read_text(encoding="utf-8")
        )
        cases = payload["cases"]
        self.assertGreaterEqual(len(cases), 10)
        self.assertLessEqual(len(cases), 20)
        self.assertGreaterEqual(len({c["source"] for c in cases}), 2)
        result = self._module().evaluate(cases)
        self.assertEqual(result["summary_only"]["fp"], 2)
        self.assertEqual(result["claim_evidence"]["fp"], 0)
        self.assertEqual(result["claim_evidence"]["fn"], 0)


if __name__ == "__main__":
    unittest.main()
