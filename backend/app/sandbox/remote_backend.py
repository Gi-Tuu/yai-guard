# 远端沙箱客户端：app VM 用它访问 sandbox VM 上的 Controller（VPC 私网 + token）。
# 只依赖标准库，便于离线测试；错误一律转成 RuntimeError，由上层记录并失败。
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from .interface import ExecResult, Limits


class RemoteSandbox:
    def __init__(self, base_url: str, token: str, *, timeout_s: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_s = timeout_s

    # ------------------------------------------------------------- lifecycle
    def create(self, task_id: str, limits: Limits) -> dict:
        payload = self._request("POST", f"/v1/sandbox/{task_id}", limits.to_dict())
        self._container_id = payload.get("container_id")
        self._image_id = payload.get("image_id")
        self._runtime = payload.get("runtime")
        return payload

    def sandbox_info(self) -> dict:
        return {
            "backend": "remote",
            "runtime": self._runtime,
            "container_id": getattr(self, "_container_id", None),
            "image_id": getattr(self, "_image_id", None),
        }

    def destroy(self, task_id: str) -> dict:
        """DELETE 必须成功且 Controller 返回 verified=true；失败向上抛，不静默吞掉。"""
        payload = self._request("DELETE", f"/v1/sandbox/{task_id}")
        if payload.get("verified") is not True:
            raise RuntimeError(f"destroy not verified by controller: {payload}")
        return payload

    # ------------------------------------------------------------------ files
    def put_file(self, task_id: str, path: str, data: bytes) -> None:
        self._request("PUT", f"/v1/sandbox/{task_id}/file?path={_q(path)}", data, raw=True)

    def get_file(self, task_id: str, path: str, *, max_bytes: int = 1_048_576,
                 allow_missing: bool = True) -> bytes:
        payload = self._request(
            "GET",
            f"/v1/sandbox/{task_id}/file?path={_q(path)}&max_bytes={max_bytes}"
            f"&allow_missing={int(allow_missing)}",
        )
        return base64.b64decode(payload.get("base64", ""))

    def list_files(self, task_id: str, path: str = "/workspace") -> list[str]:
        payload = self._request("GET", f"/v1/sandbox/{task_id}/files?path={_q(path)}")
        return list(payload.get("entries", []))

    # ------------------------------------------------------------------- exec
    def exec(self, task_id: str, command: str, *, timeout_s: int | None = None,
             cwd: str = "/workspace") -> ExecResult:
        payload = self._request("POST", f"/v1/sandbox/{task_id}/exec", {
            "command": command,
            "timeout_s": timeout_s or 120,
            "cwd": cwd,
        })
        return ExecResult(
            stdout=payload.get("stdout", ""),
            stderr=payload.get("stderr", ""),
            exit_code=int(payload.get("exit_code", 1)),
            duration_ms=int(payload.get("duration_ms", 0)),
            timed_out=bool(payload.get("timed_out", False)),
            truncated=bool(payload.get("truncated", False)),
            killed_reason=payload.get("killed_reason"),
        )

    def reap_orphans(self) -> list[str]:
        payload = self._request("POST", "/v1/orphans/reap")
        return list(payload.get("removed", []))

    def host_fingerprint(self) -> dict:
        try:
            return self._request("GET", "/healthz")
        except RuntimeError:
            return {}

    def host_snapshot(self) -> dict:
        """由 sandbox VM 的 controller 采集后回传；app 进程本身不执行任何 shell。"""
        try:
            payload = self._request("GET", "/v1/host/snapshot")
            return payload.get("snapshot") or {}
        except RuntimeError:
            return {}

    # ---------------------------------------------------------------- helpers
    def _request(self, method: str, path: str, body: dict | bytes | None = None, *, raw: bool = False):
        data = None
        headers = {"X-Sandbox-Token": self.token}
        if body is not None:
            if raw:
                data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/octet-stream"
            else:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"

        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"sandbox controller HTTP {exc.code}: {exc.read()[:200]!r}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"sandbox controller unreachable: {exc.reason}") from exc
        if payload.get("ok") is False:
            raise RuntimeError(f"sandbox controller error: {payload}")
        return payload


def _q(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
