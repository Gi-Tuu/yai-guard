# 策略引擎：Agent 与沙箱之间唯一的一道确定性闸门。
#
# 评估顺序（短路）：
#   0. 预算/时限
#   1. 工具是否已知（未知即拒绝，fail-closed）
#   2. 文件类工具 → 路径规范化 + 范围 + deny 列表
#   3. run_command → 危险正则 → argv 白名单 → rm 结构化检查 → 重定向/域名
#   4. 都没有命中 → allow
#
# 重要认知：本引擎不是「安全保证」。命令过滤可被混淆绕过；真正的保证是
# 04 文档里的容器边界（只读根、cap-drop、pids/mem 限额、--network=none）。
# 策略层的价值是「降低误操作 + 提供可解释的 UX 拦截 + 审批」。
from __future__ import annotations

import json
import posixpath
import re
from pathlib import Path
from typing import Any

from .commands import (
    SHELL_WRAPPERS,
    binary_name,
    domain_allowed,
    extract_domains,
    find_redirection_targets,
    is_private_or_metadata,
    normalize_command,
    parse_argv,
    parse_rm,
    shell_has_c,
    split_segments,
)
from .decisions import Decision, Effect, EvalContext, ToolCall
from .paths import check_path, is_within, normalize_path

KNOWN_TOOLS = {"run_command", "write_file", "read_file", "list_dir", "finish"}
FILE_WRITE_TOOLS = {"write_file"}
FILE_READ_TOOLS = {"read_file", "list_dir"}
ROOTISH_TARGETS = {"/", "/*", "~", "$HOME", "/root", "/home", "/etc", "/usr", "/var", "/bin", "/sbin", "/proc", "/sys", "/dev"}
SAFE_REDIRECT_TARGETS = {"/dev/null", "/dev/stdout", "/dev/stderr"}
# 只读类命令：任何绝对/越界路径参数都拒绝（它们没有理由跳出 /workspace）。
READ_PATH_BINARIES = {"cat", "head", "tail", "grep", "sed", "awk", "find", "stat",
                      "file", "du", "sha256sum", "ls"}


class PolicyEngine:
    """声明式策略 + 确定性评估。无状态（除配置外），可并发调用。"""

    def __init__(self, policy: dict[str, Any]):
        self.policy = policy
        self.workspace: str = policy.get("workspace", "/workspace")
        self.limits: dict[str, Any] = policy.get("limits", {})
        fs = policy.get("filesystem", {})
        self.write_roots: list[str] = fs.get("write_roots", [self.workspace])
        self.read_roots: list[str] = fs.get("read_roots", [self.workspace])
        self.path_deny: list[str] = fs.get("deny_patterns", [])
        cmd = policy.get("commands", {})
        self.deny_binaries: set[str] = set(cmd.get("deny_binaries", []))
        self.allow_binaries: set[str] = set(cmd.get("allow_binaries", []))
        self.approval_binaries: set[str] = set(cmd.get("require_approval_binaries", []))
        self.deny_patterns = [
            (item["id"], re.compile(item["pattern"], re.IGNORECASE), item.get("reason", ""), item.get("risk", "critical"))
            for item in cmd.get("deny_patterns", [])
        ]
        self.approval_patterns = [
            (item["id"], re.compile(item["pattern"], re.IGNORECASE), item.get("reason", ""), item.get("risk", "high"))
            for item in cmd.get("require_approval_patterns", [])
        ]
        net = policy.get("network", {})
        self.egress_allow: list[str] = net.get("egress_allow", [])
        self.approval_ttl: int = policy.get("approval", {}).get("ttl_s", 120)

    # ------------------------------------------------------------------ load
    @classmethod
    def from_file(cls, path: str | Path | None = None) -> "PolicyEngine":
        p = Path(path) if path else Path(__file__).with_name("default_policy.json")
        with p.open("r", encoding="utf-8") as fh:
            return cls(json.load(fh))

    # -------------------------------------------------------------- evaluate
    def evaluate(self, call: ToolCall, ctx: EvalContext | None = None) -> Decision:
        ctx = ctx or EvalContext(max_steps=int(self.limits.get("max_steps", 20)),
                                 task_timeout_s=int(self.limits.get("task_timeout_s", 900)))
        exhausted, why = ctx.budget_exhausted()
        if exhausted:
            return Decision(
                effect=Effect.DENY,
                rule_id="D-BUDGET",
                reason=why,
                risk="medium",
                suggestion="stop and report partial results instead of continuing",
            )

        if call.name not in KNOWN_TOOLS:
            return Decision(
                effect=Effect.DENY,
                rule_id="D-UNKNOWN-TOOL",
                reason=f"unknown tool: {call.name!r}",
                risk="high",
            )

        if call.name == "finish":
            return Decision(effect=Effect.ALLOW, rule_id="A-FINISH", reason="task finished", risk="low")

        if call.name in FILE_WRITE_TOOLS:
            return self._eval_file(call, write=True)
        if call.name in FILE_READ_TOOLS:
            return self._eval_file(call, write=False)
        return self._eval_command(call, ctx)

    # ------------------------------------------------------------------ file
    def _eval_file(self, call: ToolCall, *, write: bool) -> Decision:
        path = call.arguments.get("path")
        if not isinstance(path, str) or not path.strip():
            return Decision(Effect.DENY, "D-PATH-MISSING", "path argument is required", "medium")
        ok, detail, hint = check_path(
            path,
            write=write,
            workspace=self.workspace,
            write_roots=self.write_roots,
            read_roots=self.read_roots,
            deny_patterns=self.path_deny,
        )
        if not ok:
            return Decision(
                effect=Effect.DENY,
                rule_id=f"D-{hint}",
                reason=detail,
                risk="critical" if hint == "PATH_DENY" else "high",
                suggestion="stay inside /workspace; never touch credentials or host paths",
                matched=(detail,),
            )
        rule = "A-FILE-WRITE" if write else "A-FILE-READ"
        return Decision(Effect.ALLOW, rule, f"{'write' if write else 'read'} allowed: {detail}", "low", matched=(detail,))

    # --------------------------------------------------------------- command
    def _eval_command(self, call: ToolCall, ctx: EvalContext) -> Decision:
        command = call.arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return Decision(Effect.DENY, "D-CMD-EMPTY", "command is empty", "medium")

        try:
            normalized = normalize_command(command)
        except ValueError as exc:
            return Decision(Effect.DENY, "D-CMD-INVALID", str(exc), "medium")

        # 去掉引号后再做危险形态匹配：`sh -c "rm -rf /"` 这类嵌套命令的边界是引号，
        # 只按空白/分号匹配会漏。去掉引号只会让匹配更严格（fail-closed 方向）。
        scan = normalized.replace('"', "").replace("'", "")

        # 1) 危险形态正则（在去引号后的字符串上匹配，能抓住 sh -c 里的嵌套命令）
        for rule_id, pattern, reason, risk in self.deny_patterns:
            if pattern.search(scan):
                return Decision(
                    effect=Effect.DENY,
                    rule_id=rule_id,
                    reason=reason,
                    risk=risk,
                    suggestion="use a safe alternative inside /workspace; this operation is never allowed",
                    matched=(pattern.pattern,),
                )

        approval: Decision | None = None

        # 2) 逐段做 argv 级检查
        for segment in split_segments(normalized):
            argv = parse_argv(segment)
            if not argv:
                continue
            exe = binary_name(argv[0])

            if exe in self.deny_binaries:
                return Decision(
                    effect=Effect.DENY,
                    rule_id=f"D-BIN-{exe}",
                    reason=f"binary {exe!r} is never allowed in the sandbox",
                    risk="critical",
                    suggestion="use python/ls/cat/grep inside /workspace instead",
                )

            if exe == "rm":
                rm_decision = self._eval_rm(argv)
                if rm_decision.effect is Effect.DENY:
                    return rm_decision
                approval = approval or rm_decision

            if exe == "ln":
                ln_decision = self._eval_ln(argv)
                if ln_decision.effect is Effect.DENY:
                    return ln_decision
                approval = approval or ln_decision

            if exe in READ_PATH_BINARIES:
                outside = self._outside_path_arg(argv)
                if outside:
                    return Decision(
                        effect=Effect.DENY,
                        rule_id="D-ARG-OUTSIDE",
                        reason=f"path argument outside /workspace: {outside}",
                        risk="high",
                        suggestion="operate only on files inside /workspace",
                    )

            if exe in {"python", "python3"} and shell_has_c(argv):
                approval = approval or Decision(
                    Effect.REQUIRE_APPROVAL, "R-PYTHON-C",
                    "python -c executes arbitrary code strings", "high",
                    suggestion="write a .py file in /workspace and run it",
                )

            if exe in SHELL_WRAPPERS:
                approval = approval or Decision(
                    Effect.REQUIRE_APPROVAL, f"R-WRAPPER-{exe}",
                    f"shell wrapper {exe!r} can disguise the real command", "high",
                    suggestion="call the target binary directly",
                )

            if exe in self.approval_binaries:
                approval = approval or Decision(
                    Effect.REQUIRE_APPROVAL, f"R-BIN-{exe}",
                    f"binary {exe!r} requires human approval", "medium",
                    suggestion="ask the user before mutating files or installing packages",
                )
            elif exe in self.allow_binaries:
                pass
            else:
                # fail-closed：未知二进制一律要审批
                approval = approval or Decision(
                    Effect.REQUIRE_APPROVAL, "R-UNKNOWN-BIN",
                    f"binary {exe!r} is not on the allowlist", "medium",
                    suggestion="use an allowlisted binary, or ask the user to approve",
                )

        # 3) 审批型形态（shell -c / source 等）
        for rule_id, pattern, reason, risk in self.approval_patterns:
            if pattern.search(scan):
                approval = approval or Decision(Effect.REQUIRE_APPROVAL, rule_id, reason, risk)

        # 4) 重定向目标必须在 workspace 内
        for target in find_redirection_targets(normalized):
            if target in SAFE_REDIRECT_TARGETS:
                continue
            try:
                resolved = normalize_path(target, self.workspace)
            except ValueError:
                return Decision(Effect.DENY, "D-REDIRECT-INVALID", f"invalid redirect target: {target}", "high")
            if not is_within(resolved, self.workspace):
                return Decision(
                    effect=Effect.DENY,
                    rule_id="D-REDIRECT-OUTSIDE",
                    reason=f"redirect writes outside workspace: {resolved}",
                    risk="high",
                    suggestion="write outputs under /workspace",
                )

        # 5) 网络目标：内网/元数据直接拒，非白名单域名要审批
        domains = extract_domains(normalized)
        for host in domains:
            if is_private_or_metadata(host):
                return Decision(
                    effect=Effect.DENY,
                    rule_id="D-NET-PRIVATE",
                    reason=f"private/metadata address blocked: {host}",
                    risk="critical",
                )
            if not domain_allowed(host, self.egress_allow):
                approval = approval or Decision(
                    Effect.REQUIRE_APPROVAL, "R-NET-EGRESS",
                    f"egress to non-allowlisted domain: {host}", "medium",
                    suggestion="only pypi.org / files.pythonhosted.org are allowed during install",
                )

        if approval is not None:
            return approval

        return Decision(
            effect=Effect.ALLOW,
            rule_id="A-CMD-ALLOWLISTED",
            reason="command segments are allowlisted and path-safe",
            risk="low",
            matched=(normalized,),
        )

    def _outside_path_arg(self, argv: list[str]) -> str | None:
        """Resolve every non-option path argument, including relative traversal.

        Checking only absolute/`..`-prefixed arguments misses
        `cat foo/../../etc/passwd`: the shell resolves it outside /workspace,
        but the string itself starts with a normal relative component.
        """
        for arg in argv[1:]:
            if arg.startswith("-") and arg != "-":
                continue
            try:
                resolved = normalize_path(arg, self.workspace)
            except ValueError as exc:
                return f"{arg} ({exc})"
            if not is_within(resolved, self.workspace):
                return resolved
        return None

    def _eval_rm(self, argv: list[str]) -> Decision:
        recursive, force, targets = parse_rm(argv)
        if not targets:
            return Decision(Effect.DENY, "D-RM-NO-TARGET", "rm without a target", "medium")
        for target in targets:
            if target in ROOTISH_TARGETS:
                return Decision(
                    Effect.DENY, "D-RM-ROOTISH",
                    f"refusing to delete protected path: {target}", "critical",
                    suggestion="delete only specific files under /workspace",
                )
            try:
                resolved = normalize_path(target, self.workspace)
            except ValueError:
                return Decision(Effect.DENY, "D-RM-BAD-TARGET", f"invalid rm target: {target}", "high")
            if not is_within(resolved, self.workspace) or resolved.rstrip("/") == self.workspace:
                return Decision(
                    Effect.DENY, "D-RM-OUTSIDE",
                    f"rm target outside /workspace or equals workspace root: {resolved}", "critical",
                    suggestion="delete only specific files under /workspace",
                )
        if recursive:
            return Decision(
                Effect.REQUIRE_APPROVAL, "R-RM-RECURSIVE",
                f"recursive delete inside workspace: {' '.join(targets)}", "high",
                suggestion="prefer deleting specific files; ask the user first",
            )
        return Decision(
            Effect.REQUIRE_APPROVAL, "R-RM",
            f"delete requires approval: {' '.join(targets)}", "medium",
        )

    def _eval_ln(self, argv: list[str]) -> Decision:
        """symlink 目标越界直接拒绝；工作区内 symlink 仍需审批。

        为什么单独检查：symlink 是「路径看起来在 /workspace 内、运行时却指向宿主」
        最容易被忽视的绕过；策略层先拦，容器只读根/非 root 再兜底。
        """
        symlink = False
        targets: list[str] = []
        for arg in argv[1:]:
            if arg.startswith("-") and arg != "-":
                if "s" in arg.lstrip("-"):
                    symlink = True
            else:
                targets.append(arg)
        if not symlink:
            return Decision(Effect.REQUIRE_APPROVAL, "R-BIN-ln", "ln requires approval", "medium")
        if len(targets) < 2:
            return Decision(Effect.DENY, "D-SYMLINK-ARGS", "ln -s needs a target and a link name", "medium")
        source, dest = targets[0], targets[1]
        if ".." in source.split("/"):
            return Decision(
                Effect.DENY, "D-SYMLINK-TRAVERSAL",
                f"symlink target contains traversal: {source}", "critical",
                suggestion="link only to a path directly under /workspace",
            )
        try:
            resolved_dest = normalize_path(dest, self.workspace)
            resolved_source = normalize_path(source, self.workspace)
        except ValueError as exc:
            return Decision(Effect.DENY, "D-SYMLINK-INVALID", str(exc), "high")
        if not is_within(resolved_dest, self.workspace):
            return Decision(
                Effect.DENY, "D-SYMLINK-DEST",
                f"symlink destination outside /workspace: {resolved_dest}", "critical",
            )
        if source.startswith("/") and not is_within(resolved_source, self.workspace):
            return Decision(
                Effect.DENY, "D-SYMLINK-OUTSIDE",
                f"symlink target outside /workspace: {resolved_source}", "critical",
                suggestion="link only within /workspace; host paths are never allowed",
            )
        return Decision(
            Effect.REQUIRE_APPROVAL, "R-LN-SYMLINK",
            f"creating symlink {source} -> {dest}", "medium",
        )

    # ---------------------------------------------------------------- helpers
    def summary(self) -> dict[str, Any]:
        return {
            "version": self.policy.get("version", 0),
            "workspace": self.workspace,
            "limits": self.limits,
            "deny_binaries": sorted(self.deny_binaries),
            "deny_patterns": [item[0] for item in self.deny_patterns],
            "require_approval": sorted(self.approval_binaries),
            "egress_allow": self.egress_allow,
        }
