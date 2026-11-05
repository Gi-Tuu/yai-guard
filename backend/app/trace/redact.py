# 审计脱敏：入库前把疑似凭证替换掉。
# 原则：宁可过度脱敏，也不让 key 进入 SQLite/JSONL/前端。
from __future__ import annotations

import re
from typing import Any

_BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{8,}")
_SK = re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b")
_ASSIGN = re.compile(
    r"(?i)\b((?:[A-Z][A-Z0-9_]*)?"
    r"(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD|CREDENTIAL)[A-Z0-9_]*)"
    r"\s*[=:]\s*(\"[^\"]*\"|'[^']*'|[^\s\"',;]{4,})"
)


def redact_text(text: str) -> str:
    text = _BEARER.sub(r"\1***", text)
    text = _SK.sub("sk-***", text)
    text = _ASSIGN.sub(r"\1=***", text)
    return text


def redact(value: Any) -> Any:
    """递归脱敏 dict / list / str。其它类型原样返回。"""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value
