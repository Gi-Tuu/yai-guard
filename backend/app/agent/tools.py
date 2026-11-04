# 工具契约：Agent 能做的全部事情。
# 每个工具在执行前都必须经过 PolicyEngine；本模块只负责「执行」，不做安全判断。
from __future__ import annotations

import difflib
import hashlib
import posixpath
from typing import Any

from ..policy import ToolCall
from ..sandbox.interface import SandboxBackend

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command inside the isolated sandbox at /workspace. "
                           "The policy engine may deny or require approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Command line to execute in /workspace."},
                    "timeout_s": {"type": "integer", "minimum": 1, "maximum": 300},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a UTF-8 file under /workspace. Returns a unified diff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 file under /workspace (output is truncated).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files under a directory in /workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "/workspace"}},
                "required": [],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Finish the task and report a verifiable summary of what really happened.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
    },
]

TOOL_NAMES = {schema["function"]["name"] for schema in TOOL_SCHEMAS}


def make_diff(path: str, before: bytes, after: bytes) -> str:
    a = before.decode("utf-8", "replace").splitlines(keepends=True)
    b = after.decode("utf-8", "replace").splitlines(keepends=True)
    return "".join(difflib.unified_diff(a, b, fromfile=f"a{path}", tofile=f"b{path}", n=3))


def execute_tool(
    call: ToolCall,
    sandbox: SandboxBackend,
    task_id: str,
    *,
    workspace: str = "/workspace",
    max_output_bytes: int = 8192,
) -> dict[str, Any]:
    """执行一个已通过策略的工具调用。异常一律转为结构化失败，交回模型自愈。"""
    args = call.arguments or {}

    if call.name == "run_command":
        command = str(args.get("command", ""))
        timeout = int(args.get("timeout_s") or 120)
        result = sandbox.exec(task_id, command, timeout_s=timeout, cwd=workspace)
        return {
            "ok": result.exit_code == 0 and not result.timed_out,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "stdout": result.stdout[:max_output_bytes],
            "stderr": result.stderr[:max_output_bytes],
            # 供 trace 使用，回灌模型前会被 loop 移除
            "_exec": result.to_exec_dict(),
            "_argv": command,
            "_cwd": workspace,
        }

    if call.name == "write_file":
        path = posixpath.normpath(str(args.get("path", "")))
        content = str(args.get("content", ""))
        before = sandbox.get_file(task_id, path)
        sandbox.put_file(task_id, path, content.encode("utf-8"))
        return {
            "ok": True,
            "path": path,
            "bytes": len(content.encode("utf-8")),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "diff": make_diff(path, before, content.encode("utf-8"))[:max_output_bytes],
        }

    if call.name == "read_file":
        path = posixpath.normpath(str(args.get("path", "")))
        data = sandbox.get_file(task_id, path, max_bytes=max_output_bytes)
        return {"ok": True, "path": path, "content": data.decode("utf-8", "replace")}

    if call.name == "list_dir":
        path = posixpath.normpath(str(args.get("path") or workspace))
        return {"ok": True, "path": path, "entries": sandbox.list_files(task_id, path)}

    if call.name == "finish":
        return {"ok": True, "summary": str(args.get("summary", ""))}

    return {"ok": False, "error": f"unsupported tool: {call.name}"}
