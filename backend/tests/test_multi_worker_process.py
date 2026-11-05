"""True two-process multi-worker smoke test.

Starts two real uvicorn processes sharing one SQLite task DB. Process A claims a
task that blocks in approval; process B then claims and completes another task.
The assertion is on the worker_id pids recorded in SQLite, so it proves two
OS processes participated, not just two threads.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

if not __import__("importlib").util.find_spec("uvicorn"):
    raise RuntimeError("uvicorn is required for the multi-worker process test")


def _free_port() -> int:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _request(port: int, method: str, path: str,
             body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data,
        headers={"Content-Type": "application/json"}, method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def _wait_health(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            payload = _request(port, "GET", "/healthz")
            if payload.get("ok"):
                return
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.2)
    raise AssertionError(f"process on port {port} did not become healthy")


def _wait_status(port: int, task_id: str, status: str,
                 timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        last = _request(port, "GET", f"/api/tasks/{task_id}")
        if last.get("status") == status:
            return last
        time.sleep(0.2)
    raise AssertionError(f"task {task_id} did not reach {status}: {last}")


def _approval_call(port: int, task_id: str) -> str:
    raw = urllib.request.urlopen(
        f"http://127.0.0.1:{port}/api/tasks/{task_id}/trace.jsonl", timeout=10
    ).read().decode()
    for line in raw.splitlines():
        event = json.loads(line)
        if event.get("event") == "approval.requested":
            return (event.get("payload") or {})["call_id"]
    raise AssertionError("approval.requested not found")


class MultiWorkerProcessTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="yaig-multi-proc-")
        self.env = os.environ.copy()
        self.env.pop("DEMO_TOKENS", None)
        self.env.pop("DEMO_TOKEN", None)
        self.env.update({
            "ARTIFACT_DIR": self.tmp.name,
            "TASK_STORE": "sqlite",
            "TASK_RUNTIME_MODE": "multi",
            "TASK_WORKERS": "1",
            "TASK_LEASE_SECONDS": "2",
            "TASK_HEARTBEAT_SECONDS": "1",
            "MULTI_RECOVERY_INTERVAL_S": "1",
            "MOCK_MODEL": "1",
            "SANDBOX_DRIVER": "fake",
            "ALLOW_ANONYMOUS": "1",
            "RATE_LIMIT_PER_MIN": "0",
            "PYTHONUTF8": "1",
        })
        self.procs: list[subprocess.Popen] = []
        self.logs: list = []

    def tearDown(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in self.procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        for handle in self.logs:
            handle.close()
        self.tmp.cleanup()

    def _start(self, port: int) -> subprocess.Popen:
        log = open(pathlib.Path(self.tmp.name) / f"app-{port}.log", "w+",
                   encoding="utf-8")
        self.logs.append(log)
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(BACKEND), env=self.env, stdout=log, stderr=subprocess.STDOUT,
        )
        self.procs.append(proc)
        _wait_health(port)
        return proc

    def test_two_processes_claim_different_tasks(self) -> None:
        port_a, port_b = _free_port(), _free_port()
        self._start(port_a)

        first = _request(port_a, "POST", "/api/tasks",
                         {"task": "first approval task"})
        first_id = first["task_id"]
        _wait_status(port_a, first_id, "waiting_approval")

        self._start(port_b)
        second = _request(port_b, "POST", "/api/tasks",
                          {"task": "second task"})
        second_id = second["task_id"]
        _wait_status(port_b, second_id, "waiting_approval")
        call_id = _approval_call(port_b, second_id)
        _request(port_b, "POST",
                 f"/api/tasks/{second_id}/approvals/{call_id}",
                 {"approved": True})
        _wait_status(port_b, second_id, "completed")

        # Let SQLite WAL visibility settle before reading worker ids.
        time.sleep(0.3)
        conn = sqlite3.connect(str(pathlib.Path(self.tmp.name) / "tasks.db"))
        try:
            rows = conn.execute(
                "SELECT id, worker_id FROM tasks WHERE id IN (?, ?)",
                (first_id, second_id),
            ).fetchall()
        finally:
            conn.close()
        worker_ids = {row[1].split("-", 1)[0] for row in rows if row[1]}
        self.assertEqual(len(worker_ids), 2, rows)
        self.assertEqual({row[0] for row in rows}, {first_id, second_id})

    def test_worker_kill_recovers_active_lease(self) -> None:
        port_a, port_b = _free_port(), _free_port()
        first = self._start(port_a)
        created = _request(port_a, "POST", "/api/tasks",
                           {"task": "chaos lease recovery"})
        task_id = created["task_id"]
        _wait_status(port_a, task_id, "waiting_approval")
        second = self._start(port_b)

        # The first process owns the waiting lease. Kill that OS process, keep
        # the second process alive so it can run maintenance recovery.
        first.kill()
        first.wait(timeout=10)
        recovered = _wait_status(port_b, task_id, "interrupted", timeout=30)
        self.assertEqual(recovered.get("error"), "lease_expired")

        # DB recovery and trace append are separate commits/files; wait for the
        # eventual evidence write so this test does not race maintenance.
        deadline = time.monotonic() + 10
        raw = ""
        while time.monotonic() < deadline:
            raw = urllib.request.urlopen(
                f"http://127.0.0.1:{port_b}/api/tasks/{task_id}/trace.jsonl",
                timeout=10,
            ).read().decode()
            if "task.interrupted" in raw:
                break
            time.sleep(0.2)
        self.assertIn("task.interrupted", raw)
        verified = _request(port_b, "GET", f"/api/tasks/{task_id}/trace/verify")
        self.assertIn(verified.get("status"), ("CONTENT_VERIFIED", "VERIFIED"))
        self.assertTrue(verified.get("ok"), verified)
        self.assertIsNone(second.poll())
