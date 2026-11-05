"""Controller health must reflect the current Docker state, not a boot snapshot."""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import controller  # noqa: E402

try:
    from fastapi import HTTPException
    from starlette.requests import Request
except ImportError:  # pragma: no cover - dependency gate is tested elsewhere
    HTTPException = None
    Request = None


class ControllerHealthTest(unittest.TestCase):
    def setUp(self) -> None:
        with controller._HEALTH_LOCK:
            controller._HEALTH_CACHE = None

    def test_public_health_is_minimal(self) -> None:
        ready = {"daemon": {"ok": True}, "runtime": "runsc", "image": {"ok": True}}
        with mock.patch.object(controller._backend, "ensure_ready",
                               return_value=ready):
            response = controller.healthz()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.body), {"ok": True})

    def test_detailed_status_requires_token(self) -> None:
        ready = {"daemon": {"ok": True}, "runtime": "runsc", "image": {"ok": True}}
        with mock.patch.object(controller._backend, "ensure_ready",
                               return_value=ready), \
                mock.patch.object(controller, "_TOKEN", "test-token"):
            with self.assertRaises(HTTPException):
                controller.detailed_status(x_sandbox_token=None)
            response = controller.detailed_status(x_sandbox_token="test-token")
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.body)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["runtime"], "runsc")

    def test_health_reports_docker_failure(self) -> None:
        with mock.patch.object(controller._backend, "ensure_ready",
                               side_effect=RuntimeError("docker is down")):
            response = controller.healthz()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.body), {"ok": False})

    def test_health_is_short_cached(self) -> None:
        ready = {"daemon": {"ok": True}, "runtime": "runsc", "image": {"ok": True}}
        with mock.patch.object(controller._backend, "ensure_ready",
                               return_value=ready) as ensure:
            controller.healthz()
            controller.healthz()
        self.assertEqual(ensure.call_count, 1)

    def test_put_file_rejects_oversized_body(self) -> None:
        if Request is None:
            self.skipTest("fastapi/starlette unavailable")
        body = b"x" * 32
        scope = {
            "type": "http", "method": "PUT", "path": "/v1/sandbox/tk/file",
            "headers": [(b"x-sandbox-token", b"test-token")],
            "query_string": b"path=/workspace/x",
        }

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request = Request(scope, receive)
        with mock.patch.object(controller, "_TOKEN", "test-token"), \
                mock.patch.object(controller, "_MAX_FILE_BYTES", 8):
            with self.assertRaises(HTTPException) as exc:
                asyncio.run(controller.put_file("tk", "/workspace/x", request,
                                                x_sandbox_token="test-token"))
        self.assertEqual(exc.exception.status_code, 413)
