"""RemoteSandbox HTTP deadline must exceed the command deadline."""
from __future__ import annotations

import json
import pathlib
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.sandbox.remote_backend import RemoteSandbox  # noqa: E402


class _SlowHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        time.sleep(1.0)
        body = json.dumps({
            "stdout": "late", "stderr": "", "exit_code": 0,
            "duration_ms": 1000, "timed_out": False, "truncated": False,
            "killed_reason": None,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        pass


class RemoteTimeoutTest(unittest.TestCase):
    def test_exec_http_timeout_follows_command_timeout(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            sandbox = RemoteSandbox(
                f"http://127.0.0.1:{server.server_address[1]}", "token",
                timeout_s=0.2,
            )
            result = sandbox.exec("tk", "sleep 1", timeout_s=120)
            self.assertEqual(result.stdout, "late")
            self.assertFalse(result.timed_out)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
