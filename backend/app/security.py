"""Principal/admin authentication and IP rate limiting."""
from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time

from fastapi import Header, HTTPException, Request

from app import settings

_RATE_LOCK = threading.Lock()
_RATE_HITS: dict[str, list[float]] = {}


def _configured_tokens() -> set[str]:
    raw = os.environ.get("DEMO_TOKENS") or os.environ.get("DEMO_TOKEN") or ""
    return {token.strip() for token in raw.split(",") if token.strip()}


def _principal_id(token: str) -> str:
    """不把原始 token 存进任务状态/日志，只存稳定短 ID。"""
    return "u_" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _require_principal(x_demo_token: str | None = Header(default=None)) -> str:
    """统一鉴权依赖：除 /healthz、/api/policy 与静态资源外，全部任务接口都走它。

    生产模式（APP_ENV=production）没有配置 token 时在 import 阶段直接失败；
    开发/测试必须显式 ALLOW_ANONYMOUS=1 才能匿名，避免“忘记配 token 就公网裸奔”。
    """
    tokens = _configured_tokens()
    if not tokens:
        if os.environ.get("ALLOW_ANONYMOUS") == "1":
            return "anonymous"
        raise HTTPException(status_code=503, detail="server auth is not configured")
    if x_demo_token:
        for token in tokens:
            if secrets.compare_digest(x_demo_token, token):
                return _principal_id(token)
    raise HTTPException(status_code=401, detail="missing or invalid X-Demo-Token")


def _configured_admin_tokens() -> set[str]:
    raw = os.environ.get("ADMIN_TOKENS") or os.environ.get("ADMIN_TOKEN") or ""
    return {token.strip() for token in raw.split(",") if token.strip()}


def _require_admin(x_demo_token: str | None = Header(default=None)) -> str:
    """Destructive maintenance routes require an explicit admin token."""
    admins = _configured_admin_tokens()
    if not admins:
        raise HTTPException(status_code=503, detail="admin actions are not configured")
    if x_demo_token:
        for token in admins:
            if secrets.compare_digest(x_demo_token, token):
                return "admin"
    raise HTTPException(status_code=403, detail="admin token required")


if os.environ.get("APP_ENV") == "production" and not _configured_tokens():
    raise RuntimeError("APP_ENV=production requires DEMO_TOKEN or DEMO_TOKENS")


def _rate_limit_per_min() -> int:
    try:
        return int(os.environ.get("RATE_LIMIT_PER_MIN", "10"))
    except ValueError:
        return 10


def _client_ip(request: Request) -> str:
    """Resolve the real client behind Caddy without trusting arbitrary headers."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        # Caddy appends the real peer to an existing X-Forwarded-For value, so
        # the last hop is the one we cannot spoof from the browser.
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"


def _enforce_rate_limit(request: Request) -> None:
    """每 IP 滑动窗口。多 worker 模式用 SQLite 做全局配额。"""
    limit = _rate_limit_per_min()
    if limit <= 0:
        return
    ip = _client_ip(request)
    if (settings.TASK_RUNTIME_MODE == "multi"
            and settings.TASK_SERVICE.enabled
            and not settings.TASK_SERVICE.degraded):
        retry = settings.TASK_SERVICE.rate_limit(
            ip, limit=limit, window_s=settings.RATE_WINDOW_S,
        )
        if retry > 0:
            raise HTTPException(
                status_code=429,
                detail=f"rate limit exceeded ({limit} req/min)",
                headers={"Retry-After": str(retry)},
            )
        if not settings.TASK_SERVICE.degraded:
            return
        # DB quota failed; fall through to the in-process limiter rather than
        # failing open for the rest of the process lifetime.
    now = time.monotonic()
    with _RATE_LOCK:
        hits = [t for t in _RATE_HITS.get(ip, []) if now - t < settings.RATE_WINDOW_S]
        if len(hits) >= limit:
            _RATE_HITS[ip] = hits
            raise HTTPException(
                status_code=429,
                detail=f"rate limit exceeded ({limit} req/min)",
                headers={"Retry-After": str(max(1, int(settings.RATE_WINDOW_S - (now - hits[0]))))},
            )
        hits.append(now)
        _RATE_HITS[ip] = hits
