"""Task runner must never leak the global concurrency slot."""
from __future__ import annotations

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import task_runner, task_state  # noqa: E402


class TaskRunnerTest(unittest.TestCase):
    def test_trace_store_init_failure_releases_slot(self) -> None:
        state = task_state.TaskState(task_id="tk_broken", trace_id="tr_broken", task="ls")
        old = task_runner.TraceStore
        try:
            def broken(*args, **kwargs):
                raise OSError("artifacts directory is not writable")

            task_runner.TraceStore = broken  # type: ignore[assignment]
            task_runner.run_task(state)
        finally:
            task_runner.TraceStore = old  # type: ignore[assignment]
            free = task_state._EXEC_SLOTS.acquire(blocking=False)
            if free:
                task_state._EXEC_SLOTS.release()

        self.assertTrue(free, "execution slot leaked after TraceStore init failure")
        self.assertEqual(state.status, "failed")
        self.assertIn("artifacts directory", state.error or "")


if __name__ == "__main__":
    unittest.main()

