# 路径规范化与范围校验。
#
# 关键认知（写进 04 文档）：策略层的路径校验是「逻辑边界」，可被符号链接、
# 挂载点、TOCTOU 绕过；真正兜底的是沙箱的只读根文件系统 + 非 root + 无宿主挂载。
# 因此这里做的是 fail-closed 的第一道检查，而不是安全的全部。
from __future__ import annotations

import posixpath
from fnmatch import fnmatch

DEFAULT_WORKSPACE = "/workspace"


class PathViolation(ValueError):
    """路径非法（空、含 NUL、越界）。"""


def normalize_path(path: str, workspace: str = DEFAULT_WORKSPACE) -> str:
    """把用户/模型给的路径规范成容器内的绝对 POSIX 路径。

    - 相对路径按 workspace 解析
    - 反斜杠统一为正斜杠
    - 用 posixpath.normpath 消解 `.` / `..`（越界在下一层校验）
    """
    if not isinstance(path, str) or not path.strip():
        raise PathViolation("empty path")
    if "\x00" in path:
        raise PathViolation("NUL byte in path")

    raw = path.strip().replace("\\", "/")
    if not raw.startswith("/"):
        raw = posixpath.join(workspace, raw)

    norm = posixpath.normpath(raw)
    if norm in ("", "."):
        raise PathViolation("path resolves to empty")
    return norm


def is_within(path: str, root: str) -> bool:
    """path 是否等于 root 或位于 root 之下（不做符号链接解析）。"""
    root = root.rstrip("/") or "/"
    path = path.rstrip("/") or "/"
    if root == "/":
        return path.startswith("/")
    return path == root or path.startswith(root + "/")


def matches_any(path: str, patterns: list[str]) -> str | None:
    """返回第一个命中的 fnmatch 模式，用于 deny 列表。"""
    for pattern in patterns:
        if fnmatch(path, pattern) or fnmatch(path, pattern.rstrip("/") + "/*"):
            return pattern
    return None


def check_path(
    path: str,
    *,
    write: bool,
    workspace: str = DEFAULT_WORKSPACE,
    write_roots: list[str] | None = None,
    read_roots: list[str] | None = None,
    deny_patterns: list[str] | None = None,
) -> tuple[bool, str, str]:
    """校验一个文件路径。

    返回 (ok, normalized_path_or_reason, rule_hint)：
    - ok=False 时第二项是拒绝原因
    - rule_hint 供引擎拼 rule_id
    """
    try:
        norm = normalize_path(path, workspace)
    except PathViolation as exc:
        return False, str(exc), "PATH_INVALID"

    if deny_patterns:
        hit = matches_any(norm, deny_patterns)
        if hit:
            return False, f"path matches deny rule {hit}", "PATH_DENY"

    roots = write_roots if write else (read_roots or write_roots or [workspace])
    if not roots:
        roots = [workspace]
    if not any(is_within(norm, root) for root in roots):
        scope = "write" if write else "read"
        return False, f"{scope} outside allowed roots: {norm}", f"PATH_OUTSIDE_{scope.upper()}"

    return True, norm, "PATH_OK"
