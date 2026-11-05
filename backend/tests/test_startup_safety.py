"""Startup must not recover tasks before acquiring the single-writer lock."""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.tasks.models import TaskStatus  # noqa: E402
from app.tasks.store import TaskStore  # noqa: E402


class StartupSafetyTest(unittest.TestCase):
    def test_failed_runtime_lock_does_not_recover_active_task(self) -> None:
        with tempfile.TemporaryDirectory(prefix="yaig-startup-") as tmp:
            db = pathlib.Path(tmp) / "tasks.db"
            store = TaskStore(db)
            try:
                store.create_task(task_id="active", trace_id="tr_active",
                                  owner="u", prompt="x")
                store.transition("active", TaskStatus.RUNNING,
                                 worker_id="owner", lease_until=9999999999.0)
            finally:
                store.close()

            script = """
import sys, types
class FakeFcntl(types.ModuleType):
    LOCK_EX = 1
    LOCK_NB = 2
    def flock(self, *_args):
        raise OSError("lock held by another process")
sys.modules["fcntl"] = FakeFcntl("fcntl")
from app import settings
print(settings._RUNTIME_LOCK_OK)
"""
            env = os.environ.copy()
            env.update({
                "ARTIFACT_DIR": tmp,
                "TASK_STORE": "sqlite",
                "TASK_RUNTIME_MODE": "single",
                "MOCK_MODEL": "1",
                "SANDBOX_DRIVER": "fake",
                "ALLOW_ANONYMOUS": "1",
                "RATE_LIMIT_PER_MIN": "0",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(BACKEND),
            })
            result = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True,
                env=env, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip().splitlines()[-1], "False")

            conn = sqlite3.connect(db)
            try:
                status = conn.execute(
                    "SELECT status FROM tasks WHERE id = 'active'"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(status, TaskStatus.RUNNING.value)


if __name__ == "__main__":
    unittest.main()
