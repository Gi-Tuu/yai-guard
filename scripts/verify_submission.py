#!/usr/bin/env python
"""提交前自动验收：跑测试、红线、离线 demo、链接、仓库卫生、README 关键词、视频规格。

用法：
    python scripts/verify_submission.py

任何 FAIL 都会以非零退出码结束；SKIP 表示该项在当前环境无法验证（例如没有视频文件）。
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
VIDEO = ROOT / "docs" / "submission" / "video.mp4"
REQUIRED_README_WORDS = ["Vultr", "Containment", "Quickstart", "MIT", "sandbox"]
MARKDOWN_ROOTS = ["README.md", "docs", "dispatch", "research"]
SKIP_DIRS = {"artifacts", ".git", "__pycache__", ".venv", "venv"}


class Result:
    def __init__(self, name: str, status: str, detail: str = "") -> None:
        self.name = name
        self.status = status  # PASS / FAIL / SKIP
        self.detail = detail


def run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def check_tests() -> Result:
    proc = run([sys.executable, "-m", "unittest", "discover", "-s", "backend/tests", "-t", "backend"])
    if proc.returncode == 0:
        last = [line for line in proc.stderr.splitlines() if line.strip()][-1:] or ["OK"]
        return Result("unit tests", "PASS", last[0].strip())
    return Result("unit tests", "FAIL", (proc.stderr or proc.stdout).strip().splitlines()[-1] if (proc.stderr or proc.stdout).strip() else "exit != 0")


def check_redline() -> Result:
    proc = run([sys.executable, "scripts/check_no_host_exec.py"])
    ok = proc.returncode == 0
    return Result("host-exec redline", "PASS" if ok else "FAIL", (proc.stdout or proc.stderr).strip()[:200])


def check_offline_demo() -> Result:
    proc = run([sys.executable, "backend/demo_offline.py"])
    output = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0 and "hash-chain verify = True" in output
    return Result("offline demo + hash chain", "PASS" if ok else "FAIL",
                  "hash-chain verify = True" if ok else output.strip()[-300:])


def iter_markdown() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for entry in MARKDOWN_ROOTS:
        path = ROOT / entry
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            for md in path.rglob("*.md"):
                if not any(part in SKIP_DIRS for part in md.relative_to(ROOT).parts):
                    files.append(md)
    return files


LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def check_links() -> Result:
    missing: list[str] = []
    for md in iter_markdown():
        text = md.read_text(encoding="utf-8")
        for raw in LINK_RE.findall(text):
            target = raw.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "computer://")):
                continue
            if target.startswith(("/api", "<")) or "{{" in target:
                continue
            resolved = (md.parent / target).resolve()
            if not resolved.exists():
                missing.append(f"{md.relative_to(ROOT)} -> {raw}")
    if missing:
        return Result("markdown links", "FAIL", "; ".join(missing[:8]))
    return Result("markdown links", "PASS", f"{len(iter_markdown())} files checked")


def check_git_hygiene() -> Result:
    proc = run(["git", "ls-files"], timeout=60)
    if proc.returncode != 0:
        return Result("git hygiene", "SKIP", "not a git repository")
    tracked = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    bad = [p for p in tracked
           if p == ".env" or p.startswith("artifacts/") or p.endswith((".key", ".pem"))
           or (p.startswith(".env.") and p != ".env.example")]
    if bad:
        return Result("git hygiene", "FAIL", ", ".join(bad[:5]))
    return Result("git hygiene", "PASS", f"{len(tracked)} files, no secrets/artifacts tracked")


def check_license() -> Result:
    path = ROOT / "LICENSE"
    if not path.exists():
        return Result("LICENSE", "FAIL", "missing")
    text = path.read_text(encoding="utf-8")
    ok = "MIT" in text
    return Result("LICENSE", "PASS" if ok else "FAIL", "MIT found" if ok else "MIT text not found")


def check_readme() -> Result:
    path = ROOT / "README.md"
    if not path.exists():
        return Result("README keywords", "FAIL", "README.md missing")
    text = path.read_text(encoding="utf-8")
    missing = [word for word in REQUIRED_README_WORDS if word not in text]
    if missing:
        return Result("README keywords", "FAIL", "missing: " + ", ".join(missing))
    return Result("README keywords", "PASS", ", ".join(REQUIRED_README_WORDS))


def check_slides_copy() -> Result:
    slides = ROOT / "docs" / "submission" / "slides.md"
    copy = ROOT / "docs" / "submission" / "submission-copy.md"
    missing = [str(p.relative_to(ROOT)) for p in (slides, copy) if not p.exists()]
    return Result("submission docs", "PASS" if not missing else "FAIL", ", ".join(missing) or "slides + copy present")


def check_video() -> Result:
    if not VIDEO.exists():
        return Result("video spec", "SKIP", "docs/submission/video.mp4 not recorded yet")
    size = VIDEO.stat().st_size
    if size > 300 * 1024 * 1024:
        return Result("video spec", "FAIL", f"{size / 1024 / 1024:.1f} MB > 300 MB")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return Result("video spec", "SKIP", f"{size / 1024 / 1024:.1f} MB; ffprobe missing, check duration manually")
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(VIDEO)],
        capture_output=True, text=True,
    )
    try:
        duration = float(json.loads(proc.stdout)["format"]["duration"])
    except (json.JSONDecodeError, KeyError, ValueError):
        return Result("video spec", "FAIL", "cannot read duration")
    ok = duration <= 300
    return Result("video spec", "PASS" if ok else "FAIL",
                  f"{duration:.1f}s, {size / 1024 / 1024:.1f} MB")


def main() -> int:
    checks = [
        check_tests(), check_redline(), check_offline_demo(), check_links(),
        check_git_hygiene(), check_license(), check_readme(), check_slides_copy(),
        check_video(),
    ]
    width = max(len(c.name) for c in checks)
    failures = 0
    for item in checks:
        print(f"[{item.status}] {item.name.ljust(width)}  {item.detail}")
        if item.status == "FAIL":
            failures += 1
    print()
    if failures:
        print(f"RESULT: {failures} FAILED — fix before submitting")
        return 1
    print(f"RESULT: ALL CHECKS PASSED ({sum(1 for c in checks if c.status == 'SKIP')} skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
