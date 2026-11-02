"""有界输出测试：确保宿主机不会因为子进程输出而无限占用内存。"""

from __future__ import annotations

import pathlib
import sys
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.sandbox.docker_backend import run_bounded  # noqa: E402


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


class BoundedOutputTest(unittest.TestCase):
    def test_small_output_is_exact(self) -> None:
        result = run_bounded(py("print('hello')"), timeout_s=10, stdout_cap=1024,
                             stderr_cap=1024, total_cap=4096)
        self.assertEqual(result.stdout.decode().strip(), "hello")
        self.assertFalse(result.truncated)
        self.assertIsNone(result.killed_reason)

    def test_huge_stdout_is_capped_and_killed(self) -> None:
        started = time.monotonic()
        result = run_bounded(py("print('x' * 10_000_000)"), timeout_s=20,
                             stdout_cap=1024, stderr_cap=1024, total_cap=64 * 1024)
        elapsed = time.monotonic() - started
        self.assertLessEqual(len(result.stdout), 1024)
        self.assertTrue(result.stdout_truncated)
        self.assertEqual(result.killed_reason, "output_limit")
        self.assertLess(elapsed, 10, "should kill early, not buffer 10MB")

    def test_huge_stderr_is_capped(self) -> None:
        result = run_bounded(py("import sys; sys.stderr.write('e' * 10_000_000)"),
                             timeout_s=20, stdout_cap=1024, stderr_cap=1024,
                             total_cap=64 * 1024)
        self.assertLessEqual(len(result.stderr), 1024)
        self.assertTrue(result.stderr_truncated)
        self.assertEqual(result.killed_reason, "output_limit")

    def test_both_channels_do_not_deadlock(self) -> None:
        code = (
            "import sys\n"
            "for _ in range(2000):\n"
            "    sys.stdout.write('o' * 4096)\n"
            "    sys.stderr.write('e' * 4096)\n"
            "    sys.stdout.flush(); sys.stderr.flush()\n"
        )
        result = run_bounded(py(code), timeout_s=20, stdout_cap=2048, stderr_cap=2048,
                             total_cap=32 * 1024)
        self.assertLessEqual(len(result.stdout), 2048)
        self.assertLessEqual(len(result.stderr), 2048)
        self.assertTrue(result.truncated)
        self.assertIn(result.killed_reason, {"output_limit", "wall_clock"})

    def test_timeout_kills_process(self) -> None:
        started = time.monotonic()
        result = run_bounded(py("import time; time.sleep(30)"), timeout_s=0.5,
                             stdout_cap=1024, stderr_cap=1024, total_cap=4096)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.killed_reason, "wall_clock")
        self.assertLess(time.monotonic() - started, 5)


if __name__ == "__main__":
    unittest.main()
