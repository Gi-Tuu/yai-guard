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
    from app import main as api, maintenance, routes, security, settings, task_runner, task_state  # noqa: E402
from app.tasks import TaskService, TaskStatus  # noqa: E402

TOKEN_A = {"X-Demo-Token": "token-a"}
TOKEN_B = {"X-Demo-Token": "token-b"}


def _wait(client, task_id: str, headers: dict, timeout_s: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        payload = client.get(f"/api/tasks/{task_id}", headers=headers).json()
        if payload["status"] in ("completed", "failed", "cancelled"):
            return payload
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish in {timeout_s}s")


@unittest.skipUnless(TestClient is not None, "fastapi/httpx not installed")
class ApiSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(api.app)
        with security._RATE_LOCK:
            security._RATE_HITS.clear()
        os.environ["RATE_LIMIT_PER_MIN"] = "0"
        settings.POLICY.approval_ttl = 1

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
        principal = security._principal_id(token)
        task_state.TASKS[task_id] = task_state.TaskState(task_id=task_id, trace_id="tr_" + task_id,
                                           task="approval test", owner=principal)
        task_state.PENDING[(task_id, call_id)] = {
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
        with mock.patch.object(routes, "_sandbox_health", return_value={"ok": False, "error": "down"}):
            res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 503)
        self.assertFalse(res.json()["ok"])

    def test_healthz_reports_signing_key_load_failure(self) -> None:
        with mock.patch.object(settings, "TRACE_SIGNING_ERROR", "invalid key"):
            res = self.client.get("/healthz")
        self.assertEqual(res.status_code, 503)
        self.assertEqual(res.json()["signing"]["error"], "invalid key")

    def test_signing_configuration_failure_rejects_create(self) -> None:
        with mock.patch.object(settings, "TRACE_SIGNING_ERROR", "invalid key"):
            res = self.client.post(
                "/api/tasks", json={"task": "must not run unsigned"},
                headers=TOKEN_A,
            )
        self.assertEqual(res.status_code, 503)
        self.assertIn("signing", res.json()["detail"])

    def test_sqlite_unavailable_rejects_single_mode_create(self) -> None:
        service = TaskService(None, error="db down", fatal=True)
        with mock.patch.object(settings, "TASK_STORE_MODE", "sqlite"), \
                mock.patch.object(settings, "TASK_RUNTIME_MODE", "single"), \
                mock.patch.object(settings, "TASK_SERVICE", service), \
                mock.patch.object(settings, "_RUNTIME_LOCK_OK", True):
            res = self.client.post(
                "/api/tasks", json={"task": "must not enter memory"},
                headers=TOKEN_A,
            )
        self.assertEqual(res.status_code, 503)
        self.assertIn("task store unavailable", res.json()["detail"])

    def test_rate_limit_separates_forwarded_clients(self) -> None:
        os.environ["RATE_LIMIT_PER_MIN"] = "1"
        with security._RATE_LOCK:
            security._RATE_HITS.clear()
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


    def test_sqlite_task_service_bridge(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        service = TaskService.from_path(pathlib.Path(tmp.name) / "tasks.db")
        try:
            with mock.patch.object(settings, "TASK_SERVICE", service):
                created = self.client.post(
                    "/api/tasks", json={"task": "ls /workspace"}, headers=TOKEN_A
                )
                self.assertEqual(created.status_code, 200)
                task_id = created.json()["task_id"]
                _wait(self.client, task_id, TOKEN_A)
                record = service.get_record(task_id)
                self.assertIsNotNone(record)
                self.assertEqual(record.status.value, "completed")
        finally:
            service.close()
            tmp.cleanup()
    # ---------------------------------------------------------- rate limit
    def test_rate_limit_returns_429_with_retry_after(self) -> None:
        os.environ["RATE_LIMIT_PER_MIN"] = "2"
        with security._RATE_LOCK:
            security._RATE_HITS.clear()
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
    def test_tasks_are_queued_when_execution_slots_are_busy(self) -> None:
        acquired = task_state._EXEC_SLOTS.acquire(blocking=False)
        try:
            self.assertTrue(acquired)
            res = self.client.post("/api/tasks", json={"task": "ls /workspace"}, headers=TOKEN_A)
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["status"], "queued")
        finally:
            if acquired:
                task_state._EXEC_SLOTS.release()
        _wait(self.client, res.json()["task_id"], TOKEN_A)

    def test_approval_wait_releases_execution_slot(self) -> None:
        from app.agent.mock_model import MockModel
        from app.policy import ToolCall

        old_ttl = settings.POLICY.approval_ttl
        settings.POLICY.approval_ttl = 5
        models = [
            MockModel(),  # reaches the approval gate
            MockModel([ToolCall("c_finish", "finish", {"summary": "done"})]),
        ]
        try:
            with mock.patch.object(task_runner, "_build_model", side_effect=models):
                first = self.client.post(
                    "/api/tasks", json={"task": "approval wait"}, headers=TOKEN_A
                )
                first_id = first.json()["task_id"]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if any(key[0] == first_id for key in task_state.PENDING):
                        break
                    time.sleep(0.05)
                self.assertTrue(any(key[0] == first_id for key in task_state.PENDING))

                second = self.client.post(
                    "/api/tasks", json={"task": "runs while first waits"}, headers=TOKEN_A
                )
                second_id = second.json()["task_id"]
                # concurrency=1, but first is waiting for approval and must not hold the slot
                _wait(self.client, second_id, TOKEN_A, timeout_s=5)
                self.assertNotIn(
                    self.client.get(f"/api/tasks/{first_id}", headers=TOKEN_A).json()["status"],
                    ("completed", "failed"),
                )

                call_id = next(key[1] for key in task_state.PENDING if key[0] == first_id)
                approved = self.client.post(
                    f"/api/tasks/{first_id}/approvals/{call_id}",
                    json={"approved": True}, headers=TOKEN_A,
                )
                self.assertEqual(approved.status_code, 200)
                _wait(self.client, first_id, TOKEN_A, timeout_s=5)
        finally:
            settings.POLICY.approval_ttl = old_ttl

    def test_two_execution_slots_run_concurrently(self) -> None:
        from app.agent.mock_model import ModelResponse
        from app.policy import ToolCall

        barrier = threading.Barrier(2)

        class BarrierModel:
            def complete(self, messages, *, tools=None, model=None):
                barrier.wait(timeout=5)
                return ModelResponse(
                    content="ok",
                    tool_calls=[ToolCall("c_finish", "finish", {"summary": "ok"})],
                    finish_reason="tool_calls",
                )

        old_slots = task_state._EXEC_SLOTS
        task_state._EXEC_SLOTS = threading.BoundedSemaphore(2)
        try:
            with mock.patch.object(task_runner, "_build_model", side_effect=lambda: BarrierModel()):
                first = self.client.post(
                    "/api/tasks", json={"task": "concurrent 1"}, headers=TOKEN_A
                ).json()
                second = self.client.post(
                    "/api/tasks", json={"task": "concurrent 2"}, headers=TOKEN_A
                ).json()
                _wait(self.client, first["task_id"], TOKEN_A, timeout_s=8)
                _wait(self.client, second["task_id"], TOKEN_A, timeout_s=8)
        finally:
            task_state._EXEC_SLOTS = old_slots

    # ------------------------------------------------------------ cancellation
    def test_cancel_terminal_task_is_409(self) -> None:
        created = self.client.post(
            "/api/tasks", json={"task": "ls /workspace"}, headers=TOKEN_A
        ).json()
        _wait(self.client, created["task_id"], TOKEN_A)
        res = self.client.post(f"/api/tasks/{created['task_id']}/cancel", headers=TOKEN_A)
        self.assertEqual(res.status_code, 409)

    def test_cancel_wakes_approval_wait(self) -> None:
        from app.agent.mock_model import MockModel

        old_ttl = settings.POLICY.approval_ttl
        settings.POLICY.approval_ttl = 10
        try:
            with mock.patch.object(task_runner, "_build_model", side_effect=[MockModel()]):
                created = self.client.post(
                    "/api/tasks", json={"task": "approval then cancel"}, headers=TOKEN_A
                ).json()
                task_id = created["task_id"]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if any(key[0] == task_id for key in task_state.PENDING):
                        break
                    time.sleep(0.05)
                self.assertTrue(any(key[0] == task_id for key in task_state.PENDING))
                pending_verify = self.client.get(
                    f"/api/tasks/{task_id}/trace/verify", headers=TOKEN_A
                ).json()
                self.assertEqual(pending_verify["status"], "PENDING")
                self.assertFalse(pending_verify["ok"])

                cancelled = self.client.post(f"/api/tasks/{task_id}/cancel", headers=TOKEN_A)
                self.assertEqual(cancelled.status_code, 200)
                final = _wait(self.client, task_id, TOKEN_A, timeout_s=8)
                self.assertEqual(final["status"], "cancelled")

                verify = self.client.get(
                    f"/api/tasks/{task_id}/trace/verify", headers=TOKEN_A
                ).json()
                self.assertEqual(verify["status"], "CONTENT_VERIFIED")
                self.assertTrue(verify["ok"])
        finally:
            settings.POLICY.approval_ttl = old_ttl

    def test_waiting_approval_verify_is_pending_then_content_verified(self) -> None:
        from app.agent.mock_model import MockModel

        old_ttl = settings.POLICY.approval_ttl
        settings.POLICY.approval_ttl = 10
        try:
            with mock.patch.object(task_runner, "_build_model", side_effect=[MockModel()]):
                created = self.client.post(
                    "/api/tasks", json={"task": "approval verify"}, headers=TOKEN_A
                ).json()
                task_id = created["task_id"]
                deadline = time.monotonic() + 5
                call_id = None
                while time.monotonic() < deadline:
                    pending = [key[1] for key in task_state.PENDING if key[0] == task_id]
                    if pending:
                        call_id = pending[0]
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(call_id)
                pending_verify = self.client.get(
                    f"/api/tasks/{task_id}/trace/verify", headers=TOKEN_A
                ).json()
                self.assertEqual(pending_verify["status"], "PENDING")

                approved = self.client.post(
                    f"/api/tasks/{task_id}/approvals/{call_id}",
                    json={"approved": True}, headers=TOKEN_A,
                )
                self.assertEqual(approved.status_code, 200)
                final = _wait(self.client, task_id, TOKEN_A, timeout_s=10)
                self.assertEqual(final["status"], "completed")
                verify = self.client.get(
                    f"/api/tasks/{task_id}/trace/verify", headers=TOKEN_A
                ).json()
                self.assertEqual(verify["status"], "CONTENT_VERIFIED")
                self.assertTrue(verify["ok"])
                evidence = self.client.get(
                    f"/api/tasks/{task_id}/evidence", headers=TOKEN_A
                ).json()
                self.assertEqual(evidence["verification"]["status"],
                                 "CONTENT_VERIFIED")
        finally:
            settings.POLICY.approval_ttl = old_ttl

    def test_task_deadline_wakes_approval_wait(self) -> None:
        from app.agent.mock_model import MockModel

        old_ttl = settings.POLICY.approval_ttl
        old_timeout = settings.POLICY.limits.get("task_timeout_s")
        settings.POLICY.approval_ttl = 10
        settings.POLICY.limits["task_timeout_s"] = 1
        try:
            with mock.patch.object(task_runner, "_build_model", side_effect=[MockModel()]):
                created = self.client.post(
                    "/api/tasks", json={"task": "deadline while waiting"}, headers=TOKEN_A
                ).json()
                final = _wait(self.client, created["task_id"], TOKEN_A, timeout_s=6)
                self.assertEqual(final["status"], "failed")
                self.assertIn("task_timeout", final["error"] or "")
        finally:
            settings.POLICY.approval_ttl = old_ttl
            if old_timeout is not None:
                settings.POLICY.limits["task_timeout_s"] = old_timeout

    def test_requeue_queued_tasks_on_startup(self) -> None:
        from types import SimpleNamespace

        class FakeService:
            enabled = True

            def __init__(self) -> None:
                self.finished = []

            def queued_tasks(self):
                return [
                    SimpleNamespace(id="q1", trace_id="tr1", prompt="p1",
                                    owner="u1", cancel_requested=False),
                    SimpleNamespace(id="q2", trace_id="tr2", prompt="p2",
                                    owner="u1", cancel_requested=True),
                ]

            def finish(self, task_id, status, **kwargs):
                self.finished.append((task_id, status))

        class FakeExecutor:
            def __init__(self) -> None:
                self.submitted = []

            def submit(self, fn, state):
                self.submitted.append(state)

        service = FakeService()
        executor = FakeExecutor()
        with mock.patch.object(settings, "TASK_SERVICE", service), \
                mock.patch.object(task_state, "_TASK_EXECUTOR", executor), \
                mock.patch.object(task_state, "_reserve_queue_slot", return_value=True), \
                mock.patch.object(task_state, "_DISPATCHED_QUEUED", set()), \
                mock.patch.dict(task_state.TASKS, {}, clear=True):
            summary = maintenance._requeue_queued_tasks()
            self.assertEqual(summary["requeued"], ["q1"])
            self.assertEqual([s.task_id for s in executor.submitted], ["q1"])
            self.assertEqual(executor.submitted[0].status, "queued")
            self.assertIn(("q2", TaskStatus.CANCELLED), service.finished)
            self.assertIn("q1", task_state.TASKS)

            again = maintenance._requeue_queued_tasks()
            self.assertEqual(again["requeued"], [])
            self.assertEqual(len(executor.submitted), 1)
            self.assertIn({"task_id": "q1", "reason": "already_dispatched"},
                          again["skipped"])

    def test_quiesce_blocks_new_tasks(self) -> None:
        marker = settings.ARTIFACTS / "quiesce"
        try:
            marker.write_text("quiesced", encoding="utf-8")
            res = self.client.post(
                "/api/tasks", json={"task": "must not start"}, headers=TOKEN_A,
            )
            self.assertEqual(res.status_code, 503)
            self.assertIn("quiesced", res.json()["detail"])
        finally:
            marker.unlink(missing_ok=True)

    def test_metrics_require_token_and_render_text(self) -> None:
        self.assertEqual(self.client.get("/metrics").status_code, 401)
        res = self.client.get("/metrics", headers=TOKEN_A)
        self.assertEqual(res.status_code, 200)
        self.assertIn("yaig_tasks_total", res.text)
        self.assertIn("yaig_task_store_degraded", res.text)

    def test_rate_limit_falls_back_when_global_quota_store_fails(self) -> None:
        from starlette.requests import Request
        from fastapi import HTTPException

        class DownQuota:
            enabled = True
            degraded = False

            def rate_limit(self, *_args, **_kwargs):
                self.degraded = True
                return 0

        request = Request({
            "type": "http", "method": "POST", "path": "/api/tasks",
            "headers": [(b"x-forwarded-for", b"198.51.100.77")],
            "client": ("198.51.100.77", 1234),
        })
        old_limit = os.environ.get("RATE_LIMIT_PER_MIN")
        os.environ["RATE_LIMIT_PER_MIN"] = "1"
        with security._RATE_LOCK:
            security._RATE_HITS.clear()
        try:
            with mock.patch.object(settings, "TASK_RUNTIME_MODE", "multi"), \
                    mock.patch.object(settings, "TASK_SERVICE", DownQuota()):
                security._enforce_rate_limit(request)
                with self.assertRaises(HTTPException) as blocked:
                    security._enforce_rate_limit(request)
                self.assertEqual(blocked.exception.status_code, 429)
        finally:
            if old_limit is None:
                os.environ.pop("RATE_LIMIT_PER_MIN", None)
            else:
                os.environ["RATE_LIMIT_PER_MIN"] = old_limit
            with security._RATE_LOCK:
                security._RATE_HITS.clear()

    def test_db_create_failure_returns_503_without_queued_state(self) -> None:
        class BrokenCreate:
            enabled = True

            def create(self, **kwargs):
                return None

        before = len(task_state.TASKS)
        with mock.patch.object(settings, "TASK_SERVICE", BrokenCreate()):
            res = self.client.post(
                "/api/tasks", json={"task": "must not be silently queued"},
                headers=TOKEN_A,
            )
        self.assertEqual(res.status_code, 503)
        self.assertIn("task store unavailable", res.json()["detail"])
        self.assertEqual(len(task_state.TASKS), before)

    def test_maintenance_reap_requires_admin_token(self) -> None:
        old_admin = os.environ.pop("ADMIN_TOKEN", None)
        old_admins = os.environ.pop("ADMIN_TOKENS", None)
        try:
            denied = self.client.post("/api/maintenance/reap", json={"dry_run": True},
                                      headers=TOKEN_A)
            self.assertIn(denied.status_code, (403, 503))
            with mock.patch.dict(os.environ, {"ADMIN_TOKEN": "audit-admin"}):
                allowed = self.client.post(
                    "/api/maintenance/reap", json={"dry_run": True},
                    headers={"X-Demo-Token": "audit-admin"},
                )
                self.assertEqual(allowed.status_code, 200)
                self.assertTrue(allowed.json()["skipped_reason"])
        finally:
            if old_admin is not None:
                os.environ["ADMIN_TOKEN"] = old_admin
            if old_admins is not None:
                os.environ["ADMIN_TOKENS"] = old_admins

    def test_api_dispatch_is_tracked_to_prevent_requeue_duplicate(self) -> None:
        from types import SimpleNamespace

        class FakeService:
            enabled = True

            def create(self, **kwargs):
                return SimpleNamespace(id=kwargs["task_id"])

            def queued_tasks(self):
                return [SimpleNamespace(id=self.task_id, trace_id="tr", prompt="x",
                                        owner="u", cancel_requested=False)]

        class FakeExecutor:
            def __init__(self) -> None:
                self.submitted = []

            def submit(self, fn, state):
                self.submitted.append(state)

        service = FakeService()
        executor = FakeExecutor()
        with mock.patch.object(settings, "TASK_SERVICE", service), \
                mock.patch.object(task_state, "_TASK_EXECUTOR", executor), \
                mock.patch.object(task_state, "_reserve_queue_slot", return_value=True), \
                mock.patch.object(task_state, "_DISPATCHED_QUEUED", set()), \
                mock.patch.dict(task_state.TASKS, {}, clear=True):
            created = self.client.post(
                "/api/tasks", json={"task": "queued audit"}, headers=TOKEN_A,
            )
            self.assertEqual(created.status_code, 200)
            task_id = created.json()["task_id"]
            service.task_id = task_id
            self.assertEqual(len(executor.submitted), 1)
            self.assertIn(task_id, task_state._DISPATCHED_QUEUED)
            summary = maintenance._requeue_queued_tasks()
            self.assertEqual(summary["requeued"], [])
            self.assertEqual(len(executor.submitted), 1)

    def test_multi_mode_create_only_enqueues(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        service = TaskService.from_path(pathlib.Path(tmp.name) / "tasks.db")

        class FakeExecutor:
            def __init__(self) -> None:
                self.calls = 0

            def submit(self, fn, state):
                self.calls += 1

        executor = FakeExecutor()
        try:
            with mock.patch.object(settings, "TASK_SERVICE", service), \
                    mock.patch.object(settings, "TASK_RUNTIME_MODE", "multi"), \
                    mock.patch.object(task_state, "_TASK_EXECUTOR", executor), \
                    mock.patch.dict(task_state.TASKS, {}, clear=True):
                created = self.client.post(
                    "/api/tasks", json={"task": "queued multi"}, headers=TOKEN_A,
                )
                self.assertEqual(created.status_code, 200)
                self.assertEqual(created.json()["status"], "queued")
                self.assertEqual(executor.calls, 0)
                self.assertEqual(service.count_queued(), 1)
        finally:
            service.close()
            tmp.cleanup()

    def test_db_event_store_and_db_approval_roundtrip(self) -> None:
        from app.agent.mock_model import MockModel

        tmp = tempfile.TemporaryDirectory()
        service = TaskService.from_path(pathlib.Path(tmp.name) / "tasks.db")
        old_ttl = settings.POLICY.approval_ttl
        settings.POLICY.approval_ttl = 5
        try:
            with mock.patch.object(settings, "TASK_SERVICE", service), \
                    mock.patch.object(settings, "TASK_APPROVAL_STORE", "db"), \
                    mock.patch.object(settings, "TASK_EVENT_STORE", "db"), \
                    mock.patch.object(task_runner, "_build_model", side_effect=[MockModel()]):
                created = self.client.post(
                    "/api/tasks", json={"task": "db approval"}, headers=TOKEN_A,
                ).json()
                task_id = created["task_id"]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if service.pending_approvals(task_id):
                        break
                    time.sleep(0.05)
                pending = service.pending_approvals(task_id)
                self.assertEqual(len(pending), 1)
                approved = self.client.post(
                    f"/api/tasks/{task_id}/approvals/{pending[0]['call_id']}",
                    json={"approved": True}, headers=TOKEN_A,
                )
                self.assertEqual(approved.status_code, 200)
                final = _wait(self.client, task_id, TOKEN_A, timeout_s=8)
                self.assertEqual(final["status"], "completed")
                self.assertGreater(service.count_events(task_id), 0)
        finally:
            settings.POLICY.approval_ttl = old_ttl
            service.close()
            tmp.cleanup()

    # --------------------------------------------------------------- trace
    def test_trace_tamper_is_detected(self) -> None:
        created = self.client.post("/api/tasks", json={"task": "ls /workspace"},
                                   headers=TOKEN_A).json()
        task_id, trace_id = created["task_id"], created["trace_id"]
        _wait(self.client, task_id, TOKEN_A)

        ok = self.client.get(f"/api/tasks/{task_id}/trace/verify", headers=TOKEN_A).json()
        self.assertTrue(ok["ok"])
        self.assertIsNone(ok["bad_seq"])

        path = settings.TRACES / f"{trace_id}.jsonl"
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


