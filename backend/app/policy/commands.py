# 命令解析与危险形态识别。
#
# 设计原则（与 04 文档 §5.2 一致）：
# 1. 先对「原始字符串」做危险正则（防止 `sh -c "rm -rf /"` 这类嵌套绕过）；
# 2. 再 shlex 拆 argv 做二进制白名单（比纯正则稳）；
# 3. 对 `rm` 做结构化目标检查（正则难以覆盖所有参数排列）；
# 4. 识别 shell wrapper / 重定向 / 网络目标。
# 注意：本模块不执行任何命令，只做文本分析。
from __future__ import annotations

import ipaddress
import posixpath
import re
import shlex

IFS_RE = re.compile(r"\$\{IFS\}|\$IFS")
SEPARATOR_RE = re.compile(r"(?:&&|\|\||;|\n|\|)")
REDIRECT_RE = re.compile(r"(?:^|\s)(?:[0-9]?>|>>|&>)\s*([^\s;&|]+)")
URL_RE = re.compile(r"https?://([^/\s'\"`]+)", re.IGNORECASE)
SHELL_BINARIES = {"sh", "bash", "zsh", "dash", "ksh", "fish", "ash"}
SHELL_WRAPPERS = {"env", "command", "exec", "nice", "nohup", "timeout", "xargs", "stdbuf"}


def normalize_command(command: str) -> str:
    """把命令规范化到可比较的形态（仅用于分析，绝不执行）。

    - 去掉 NUL
    - `${IFS}` / `$IFS` 还原为空格（最常见的绕过手法之一）
    - 压缩空白
    """
    if not isinstance(command, str):
        raise ValueError("command must be a string")
    cmd = command.replace("\x00", "")
    cmd = IFS_RE.sub(" ", cmd)
    cmd = re.sub(r"[ \t]+", " ", cmd)
    return cmd.strip()


def split_segments(command: str) -> list[str]:
    """按 shell 控制符粗分为多段。只用于策略判断，不保证与真实 shell 语义一致。"""
    return [seg.strip() for seg in SEPARATOR_RE.split(command) if seg.strip()]


def parse_argv(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        # 引号不闭合等：退化为按空白切分，仍然做检查（fail-closed 方向）
        return segment.split()


def binary_name(token: str) -> str:
    return posixpath.basename(token)


def parse_rm(argv: list[str]) -> tuple[bool, bool, list[str]]:
    """解析 rm 参数，返回 (recursive, force, targets)。"""
    recursive = force = False
    targets: list[str] = []
    for arg in argv[1:]:
        if arg == "--":
            continue
        if arg.startswith("-") and arg != "-":
            flags = arg.lstrip("-")
            recursive = recursive or ("r" in flags or "R" in flags or arg == "--recursive")
            force = force or ("f" in flags or arg == "--force")
        else:
            targets.append(arg)
    return recursive, force, targets


def shell_has_c(argv: list[str]) -> bool:
    return any(a == "-c" for a in argv[:3])


def find_redirection_targets(command: str) -> list[str]:
    return [m.group(1) for m in REDIRECT_RE.finditer(command)]


def extract_domains(command: str) -> list[str]:
    return [m.group(1).split(":")[0].lower() for m in URL_RE.finditer(command)]


def is_private_or_metadata(host: str) -> bool:
    """内网/环回/链路本地/云元数据地址一律视为危险。"""
    host = host.lower().strip("[]")
    if host in {"localhost", "metadata.google.internal", "metadata"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or str(ip) == "169.254.169.254"
    )


def domain_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower()
    for item in allowed:
        item = item.lower().lstrip(".")
        if host == item or host.endswith("." + item):
            return True
    return False
