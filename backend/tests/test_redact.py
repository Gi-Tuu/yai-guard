"""Trace redaction edge cases."""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.trace.redact import redact_text  # noqa: E402


class RedactTest(unittest.TestCase):
    def test_common_credential_forms_are_removed(self) -> None:
        self.assertNotIn("secret", redact_text("PASSWORD=secret value"))
        self.assertNotIn("abc12345", redact_text("DB_PASSWD=abc12345"))
        self.assertNotIn("hello", redact_text("apiKey='hello world'"))
        self.assertNotIn("abcdefghijk", redact_text(
            "Authorization: Bearer abcdefghijk"
        ))

    def test_non_credential_path_is_not_over_redacted(self) -> None:
        self.assertEqual(redact_text("PATH=/usr/bin"), "PATH=/usr/bin")
