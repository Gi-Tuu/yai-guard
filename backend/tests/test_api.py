"""API 安全测试：统一鉴权、任务归属、严格审批布尔、限流、并发、trace 端点。

依赖 fastapi/httpx；缺失时整体跳过。CI 的 api-security job 会安装依赖并设置
REQUIRE_API_TESTS=1，确保这些测试真正执行而不是被绿色总状态掩盖。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

REQUIRE_API = os.environ.get("REQUIRE_API_TESTS") == "1"
if TestClient is None and REQUIRE_API:
    raise RuntimeError("REQUIRE_API_TESTS=1 but fastapi/httpx are not installed")

os.environ["ARTIFACT_DIR"] = tempfile.mkdtemp(prefix="yaig-api-")
os.environ.setdefault("MOCK_MODEL", "1")
os.environ.setdefault("SANDBOX_DRIVER", "fake")
os.environ.setdefault("DEMO_TOKENS", "token-a,token-b")
os.environ.setdefault("RATE_LIMIT_PER_MIN", "0")

if TestClient is not None:  # 依赖齐全才导入 app
    from app import main as api  # noqa: E402

TOKEN_A = {"X-Demo-Token": "token-a"}
TOKEN_B = {"X-Demo-Token": "token-b"}


def _wait(client, task_id: str, headers: dict, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        payload = client.get(f"/api/tasks/{task_id}", headers=headers).json()
        if payload["status"] in ("completed", "failed"):
            return payload
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish in {timeout_s}s")


@unittest.skipUnless(TestClient is not None, "fastapi/httpx not installed")
class ApiSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(api.app)
        with api._RATE_LOCK:
            api._RATE_HITS.clear()
        os.environ["RATE_LIMIT_PER_MIN"] = "0"
        api.POLICY.approval_ttl = 1

    # ------------------------------------------------------------- public
    def test_healthz_is_public(self) -> None:
        self.assertEqual(self.client.get("/healthz").status_code, 200)

    def test_policy_is_public(self) -> None:
        self.assertEqual(self.client.get("/api/policy").status_code, 200)

    # ----------------------------------------------------------------- auth
    def test_create_task_without_token_is_401(self) -> None:
        self.assertEqual(self.client.post("/api/tasks", json={"task": "ls"}).status_code, 401)

    def test_create_task_with_wrong_token_is_401(self) -> None:
        res = self.client.post("/api/tasks", json={"task": "ls"},
                               headers={"X-Demo-Token": "nope"})
        self.assertEqual(res.status_code, 401)

    def test_create_task_with_token_is_200(self) -> None:
        res = self.client.post("/api/tasks", json={"task": "ls /workspace"}, headers=TOKEN_A)
        self.assertEqual(res.status_code, 200)
        _wait(self.client, res.json()["task_id"], TOKEN_A)

    def test_task_read_requires_token_and_owner(self) -> None:
        task_id = self.client.post("/api/tasks", json={"task": "ls /workspace"},
                                   headers=TOKEN_A).json()["task_id"]
        _wait(self.client, task_id, TOKEN_A)

        self.assertEqual(self.client.get(f"/api/tasks/{task_id}").status_code, 401)
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}",
                                         headers={"X-Demo-Token": "nope"}).status_code, 401)
        # 另一个合法 token 也不能读 A 的任务
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}", headers=TOKEN_B).status_code, 404)
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}", headers=TOKEN_A).status_code, 200)

    def test_sse_requires_token_and_owner(self) -> None:
        task_id = self.client.post("/api/tasks", json={"task": "ls /workspace"},
                                   headers=TOKEN_A).json()["task_id"]
        _wait(self.client, task_id, TOKEN_A)
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}/events").status_code, 401)
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}/events", headers=TOKEN_B).status_code, 404)
        res = self.client.get(f"/api/tasks/{task_id}/events", headers=TOKEN_A)
        self.assertEqual(res.status_code, 200)
        self.assertIn("data:", res.text)

    def test_trace_endpoints_require_token_and_owner(self) -> None:
        task_id = self.client.post("/api/tasks", json={"task": "ls /workspace"},
                                   headers=TOKEN_A).json()["task_id"]
        _wait(self.client, task_id, TOKEN_A)

        self.assertEqual(self.client.get(f"/api/tasks/{task_id}/trace.jsonl").status_code, 401)
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}/trace.jsonl",
                                         headers=TOKEN_B).status_code, 404)
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}/trace.jsonl",
                                         headers=TOKEN_A).status_code, 200)
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}/trace/verify").status_code, 401)
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}/trace/verify",
                                         headers=TOKEN_A).status_code, 200)

    # ------------------------------------------------------------ approval
    def _seed_pending(self, task_id: str = "tk_approval", call_id: str = "call_1",
                      token: str = "token-a") -> tuple[str, str]:
        principal = api._principal_id(token)
        api.TASKS[task_id] = api.TaskState(task_id=task_id, trace_id="tr_" + task_id,
                                           task="approval test", owner=principal)
        api.PENDING[(task_id, call_id)] = {
            "event": threading.Event(), "approved": False, "resolved": False,
            "owner": principal, "decision": {}, "call": {},
        }
        return task_id, call_id

    def test_approval_accepts_json_true_and_false(self) -> None:
        for value in (True, False):
            task_id, call_id = self._seed_pending("tk_a_" + str(value), "c1")
            res = self.client.post(f"/api/tasks/{task_id}/approvals/{call_id}",
                                   json={"approved": value}, headers=TOKEN_A)
            self.assertEqual(res.status_code, 200)
            self.assertIs(res.json()["approved"], value)

    def test_approval_rejects_non_boolean(self) -> None:
        for body in ({"approved": "false"}, {"approved": "true"},
                     {"approved": 1}, {"approved": 0}, {"approved": None}, {}):
            task_id, call_id = self._seed_pending("tk_bad", "c1")
            res = self.client.post(f"/api/tasks/{task_id}/approvals/{call_id}",
                                   json=body, headers=TOKEN_A)
            self.assertEqual(res.status_code, 422, body)

    def test_approval_duplicate_is_409(self) -> None:
        task_id, call_id = self._seed_pending("tk_dup", "c1")
        first = self.client.post(f"/api/tasks/{task_id}/approvals/{call_id}",
                                 json={"approved": True}, headers=TOKEN_A)
        self.assertEqual(first.status_code, 200)
        second = self.client.post(f"/api/tasks/{task_id}/approvals/{call_id}",
                                  json={"approved": False}, headers=TOKEN_A)
        self.assertEqual(second.status_code, 409)

    def test_approval_wrong_owner_is_404(self) -> None:
        task_id, call_id = self._seed_pending("tk_owner", "c1")
        res = self.client.post(f"/api/tasks/{task_id}/approvals/{call_id}",
                               json={"approved": True}, headers=TOKEN_B)
        self.assertEqual(res.status_code, 404)

    def test_approval_unknown_call_is_404(self) -> None:
        task_id, _ = self._seed_pending("tk_unknown", "c1")
        res = self.client.post(f"/api/tasks/{task_id}/approvals/nope",
                               json={"approved": True}, headers=TOKEN_A)
        self.assertEqual(res.status_code, 404)

    def test_healthz_reports_sandbox_failure(self) -> None:
        with mock.patch.object(api, "_sandbox_health", return_value={"ok": False, "error": "down"}):
            res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 503)
        self.assertFalse(res.json()["ok"])

    def test_rate_limit_separates_forwarded_clients(self) -> None:
        os.environ["RATE_LIMIT_PER_MIN"] = "1"
        with api._RATE_LOCK:
            api._RATE_HITS.clear()
        first = self.client.post("/api/tasks", json={"task": "ls /workspace"},
                                 headers={**TOKEN_A, "X-Forwarded-For": "198.51.100.1"})
        self.assertEqual(first.status_code, 200)
        _wait(self.client, first.json()["task_id"], TOKEN_A)
        second = self.client.post("/api/tasks", json={"task": "ls /workspace"},
                                  headers={**TOKEN_A, "X-Forwarded-For": "198.51.100.2"})
        self.assertEqual(second.status_code, 200)
        _wait(self.client, second.json()["task_id"], TOKEN_A)
        third = self.client.post("/api/tasks", json={"task": "ls /workspace"},
                                 headers={**TOKEN_A, "X-Forwarded-For": "198.51.100.1"})
        self.assertEqual(third.status_code, 429)
        os.environ["RATE_LIMIT_PER_MIN"] = "0"

    # ---------------------------------------------------------- rate limit
    def test_rate_limit_returns_429_with_retry_after(self) -> None:
        os.environ["RATE_LIMIT_PER_MIN"] = "2"
        with api._RATE_LOCK:
            api._RATE_HITS.clear()
        codes = []
        for _ in range(3):
            res = self.client.post("/api/tasks", json={"task": "ls /workspace"}, headers=TOKEN_A)
            codes.append(res.status_code)
            if res.status_code == 200:
                _wait(self.client, res.json()["task_id"], TOKEN_A)
        self.assertEqual(codes[:2], [200, 200])
        self.assertEqual(codes[2], 429)
        self.assertIn("Retry-After", res.headers)

    # --------------------------------------------------------- concurrency
    def test_second_task_is_rejected_while_slot_is_busy(self) -> None:
        acquired = api._TASK_SLOT.acquire(blocking=False)
        try:
            self.assertTrue(acquired)
            res = self.client.post("/api/tasks", json={"task": "ls /workspace"}, headers=TOKEN_A)
            self.assertEqual(res.status_code, 429)
            self.assertIn("concurrency", res.json()["detail"])
        finally:
            if acquired:
                api._TASK_SLOT.release()

    # --------------------------------------------------------------- trace
    def test_trace_tamper_is_detected(self) -> None:
        created = self.client.post("/api/tasks", json={"task": "ls /workspace"},
                                   headers=TOKEN_A).json()
        task_id, trace_id = created["task_id"], created["trace_id"]
        _wait(self.client, task_id, TOKEN_A)

        ok = self.client.get(f"/api/tasks/{task_id}/trace/verify", headers=TOKEN_A).json()
        self.assertTrue(ok["ok"])
        self.assertIsNone(ok["bad_seq"])

        path = api.TRACES / f"{trace_id}.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        first["payload"]["task"] = "tampered"
        lines[0] = json.dumps(first, sort_keys=True, separators=(",", ":"))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        bad = self.client.get(f"/api/tasks/{task_id}/trace/verify", headers=TOKEN_A).json()
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["bad_seq"], 1)

    def test_empty_task_is_400(self) -> None:
        res = self.client.post("/api/tasks", json={"task": "   "}, headers=TOKEN_A)
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
