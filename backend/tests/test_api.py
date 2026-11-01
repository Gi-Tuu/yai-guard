"""API 加固测试：DEMO_TOKEN 鉴权、每 IP 限流、全局并发=1、trace 校验端点。

fastapi / httpx 未安装时整体 skip（不 fail，避免 CI 卡在环境上）。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - 环境缺依赖时跳过
    TestClient = None

ARTIFACT_DIR = tempfile.mkdtemp(prefix="yaig-api-")
os.environ["ARTIFACT_DIR"] = ARTIFACT_DIR
os.environ.setdefault("MOCK_MODEL", "1")
os.environ.setdefault("SANDBOX_DRIVER", "fake")
os.environ.setdefault("RATE_LIMIT_PER_MIN", "0")

if TestClient is not None:  # 只有依赖齐全时才导入 app
    from app import main as api  # noqa: E402


def _wait(client: "TestClient", task_id: str, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        payload = client.get(f"/api/tasks/{task_id}").json()
        if payload["status"] in ("completed", "failed"):
            return payload
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish in {timeout_s}s")


@unittest.skipUnless(TestClient is not None, "fastapi/httpx not installed")
class ApiHardeningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(api.app)
        with api._RATE_LOCK:
            api._RATE_HITS.clear()
        os.environ["RATE_LIMIT_PER_MIN"] = "0"
        os.environ.pop("DEMO_TOKEN", None)
        # 审批等待会阻塞任务线程；测试里把 TTL 降到 1s，让默认「拒绝」快速回落
        api.POLICY.approval_ttl = 1

    def tearDown(self) -> None:
        os.environ.pop("DEMO_TOKEN", None)
        os.environ["RATE_LIMIT_PER_MIN"] = "0"
        with api._RATE_LOCK:
            api._RATE_HITS.clear()

    # ------------------------------------------------------------------ auth
    def test_missing_token_is_401(self) -> None:
        os.environ["DEMO_TOKEN"] = "demo-secret"
        res = self.client.post("/api/tasks", json={"task": "ls /workspace"})
        self.assertEqual(res.status_code, 401)

    def test_wrong_token_is_401(self) -> None:
        os.environ["DEMO_TOKEN"] = "demo-secret"
        res = self.client.post("/api/tasks", json={"task": "ls /workspace"},
                               headers={"X-Demo-Token": "nope"})
        self.assertEqual(res.status_code, 401)

    def test_correct_token_is_accepted(self) -> None:
        os.environ["DEMO_TOKEN"] = "demo-secret"
        res = self.client.post("/api/tasks", json={"task": "ls /workspace"},
                               headers={"X-Demo-Token": "demo-secret"})
        self.assertEqual(res.status_code, 200)
        _wait(self.client, res.json()["task_id"])

    # -------------------------------------------------------------- rate limit
    def test_rate_limit_returns_429_with_retry_after(self) -> None:
        os.environ["RATE_LIMIT_PER_MIN"] = "2"
        with api._RATE_LOCK:
            api._RATE_HITS.clear()
        codes = []
        for _ in range(3):
            res = self.client.post("/api/tasks", json={"task": "ls /workspace"})
            codes.append(res.status_code)
            if res.status_code == 200:
                _wait(self.client, res.json()["task_id"])
        self.assertEqual(codes[:2], [200, 200])
        self.assertEqual(codes[2], 429)
        self.assertIn("Retry-After", res.headers)

    # ----------------------------------------------------------- concurrency
    def test_second_task_is_rejected_while_slot_is_busy(self) -> None:
        acquired = api._TASK_SLOT.acquire(blocking=False)
        try:
            self.assertTrue(acquired)
            res = self.client.post("/api/tasks", json={"task": "ls /workspace"})
            self.assertEqual(res.status_code, 429)
            self.assertIn("concurrency", res.json()["detail"])
        finally:
            if acquired:
                api._TASK_SLOT.release()

    # --------------------------------------------------------------- trace
    def test_trace_verify_endpoint_and_tamper_detection(self) -> None:
        res = self.client.post("/api/tasks", json={"task": "ls /workspace"})
        task_id = res.json()["task_id"]
        trace_id = res.json()["trace_id"]
        _wait(self.client, task_id)

        ok = self.client.get(f"/api/tasks/{task_id}/trace/verify").json()
        self.assertTrue(ok["ok"])
        self.assertIsNone(ok["bad_seq"])
        self.assertGreater(ok["events"], 5)

        path = api.TRACES / f"{trace_id}.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["payload"]["task"] = "tampered"
        lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        bad = self.client.get(f"/api/tasks/{task_id}/trace/verify").json()
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["bad_seq"], 1)

    def test_healthz_is_not_rate_limited(self) -> None:
        os.environ["RATE_LIMIT_PER_MIN"] = "1"
        for _ in range(5):
            self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_empty_task_is_400(self) -> None:
        res = self.client.post("/api/tasks", json={"task": "   "})
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
