"""Unit checks for controller orphan reaping without a Docker daemon."""
from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.sandbox.docker_backend import DockerError, DockerSandbox  # noqa: E402


class DockerReaperUnitTest(unittest.TestCase):
    def test_reap_orphans_does_not_report_unverified_removal(self) -> None:
        sandbox = DockerSandbox()
        listing = subprocess.CompletedProcess(
            args=["docker"], returncode=0, stdout="yaig-tk_orphan\n", stderr="",
        )
        with mock.patch("app.sandbox.docker_backend.subprocess.run",
                        return_value=listing), \
                mock.patch.object(sandbox, "destroy",
                                  return_value={"verified": False, "error": "still exists"}):
            with self.assertRaises(DockerError):
                sandbox.reap_orphans()

    def test_reap_orphans_reports_only_verified_removals(self) -> None:
        sandbox = DockerSandbox()
        listing = subprocess.CompletedProcess(
            args=["docker"], returncode=0,
            stdout="yaig-tk_one\nyaig-tk_two\n", stderr="",
        )
        with mock.patch("app.sandbox.docker_backend.subprocess.run",
                        return_value=listing), \
                mock.patch.object(sandbox, "destroy",
                                  side_effect=[{"verified": True}, {"verified": True}]):
            self.assertEqual(sandbox.reap_orphans(), ["yaig-tk_one", "yaig-tk_two"])
