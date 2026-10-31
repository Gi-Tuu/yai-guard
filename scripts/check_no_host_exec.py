#!/usr/bin/env python
"""红线静态检查：确保「宿主机执行」只存在于 sandbox 后端。

规则（对应 05 文档 §12）：
- app/agent/、app/policy/、app/trace/、app/main.py 不允许出现
  subprocess / os.system / os.popen / eval( / exec(
- 只有 app/sandbox/ 与 app/controller.py 可以执行命令

用法：python scripts/check_no_host_exec.py
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "backend" / "app"

FORBIDDEN = re.compile(r"\b(subprocess|os\.system|os\.popen)\b|(?<![.\w])(eval|exec)\s*\(")
EXEMPT_DIRS = {APP / "sandbox"}
EXEMPT_FILES = {APP / "controller.py"}

# 允许的注释里出现关键词解释；只在代码行上判断
COMMENT_PREFIXES = ("#", '"""', "'''")


def is_exempt(path: pathlib.Path) -> bool:
    return path in EXEMPT_FILES or any(path.is_relative_to(d) for d in EXEMPT_DIRS)


def scan() -> list[tuple[str, int, str]]:
    violations: list[tuple[str, int, str]] = []
    for path in sorted(APP.rglob("*.py")):
        if is_exempt(path):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(COMMENT_PREFIXES):
                continue
            if FORBIDDEN.search(line):
                violations.append((str(path.relative_to(ROOT)), lineno, stripped))
    return violations


def main() -> int:
    violations = scan()
    if violations:
        print(f"FAIL: {len(violations)} forbidden execution primitive(s) outside sandbox layer")
        for path, lineno, line in violations:
            print(f"  {path}:{lineno}: {line}")
        return 1
    print("OK: no host-execution primitives outside app/sandbox/ and app/controller.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
