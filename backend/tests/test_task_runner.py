"""Task runner must never leak the global concurrency slot."""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import main as api  # noqa: E402


class TaskRunnerTest(unittest.TestCase):
    def test_trace_store_init_failure_releases_slot(self) -> None:
        acquired = api._TASK_SLOT.acquire(blocking=False)
        self.assertTrue(acquired)
        state = api.TaskState(task_id="tk_broken", trace_id="tr_broken", task="ls")
        old = api.TraceStore
        try:
            def broken(*args, **kwargs):
                raise OSError("artifacts directory is not writable")

            api.TraceStore = broken  # type: ignore[assignment]
            api._run_task(state)
        finally:
            api.TraceStore = old  # type: ignore[assignment]
            free = api._TASK_SLOT.acquire(blocking=False)
            if free:
                api._TASK_SLOT.release()

        self.assertTrue(free, "slot leaked after TraceStore init failure")
        self.assertEqual(state.status, "failed")
        self.assertIn("artifacts directory", state.error or "")


if __name__ == "__main__":
    unittest.main()
