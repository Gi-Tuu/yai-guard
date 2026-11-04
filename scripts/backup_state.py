#!/usr/bin/env python
"""Consistent backup/restore for the durable task store and traces.

Backup uses sqlite3's online backup API, so it does not copy a half-written DB.
Archives contain ``tasks.db``, ``traces/`` and a manifest with SHA-256 checksums.

Examples:
    python scripts/backup_state.py backup --artifacts artifacts --out backups
    python scripts/backup_state.py restore backups/yai-guard-YYYY....tar.gz \
        --artifacts artifacts --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime, timezone


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_snapshot(source_db: pathlib.Path, target_db: pathlib.Path) -> None:
    source = sqlite3.connect(str(source_db))
    target = sqlite3.connect(str(target_db))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _tree_fingerprint(root: pathlib.Path) -> dict[str, tuple[int, int]]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): (path.stat().st_mtime_ns, path.stat().st_size)
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def backup(artifacts: pathlib.Path, out_dir: pathlib.Path,
           *, allow_active: bool = False) -> pathlib.Path:
    artifacts = artifacts.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        out_dir.chmod(0o700)
    except OSError:
        pass
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = out_dir / f"yai-guard-{stamp}.tar.gz"
    manifest: dict = {"schema": "yai-guard-backup/v1", "created_at": stamp,
                      "consistent": not allow_active, "files": {}}
    with tempfile.TemporaryDirectory(prefix="yaig-backup-") as tmp:
        stage = pathlib.Path(tmp)
        db = artifacts / "tasks.db"
        traces = artifacts / "traces"
        first_db_hash: str | None = None
        if db.exists():
            _sqlite_snapshot(db, stage / "tasks.db")
            first_db_hash = _sha256(stage / "tasks.db")
        before = _tree_fingerprint(traces)
        if traces.exists():
            shutil.copytree(traces, stage / "traces")
        after = _tree_fingerprint(traces)
        if not allow_active:
            if before != after:
                raise RuntimeError("traces changed during backup; retry when the app is quiescent")
            if db.exists():
                check_db = stage / "tasks.db.check"
                _sqlite_snapshot(db, check_db)
                try:
                    if _sha256(check_db) != first_db_hash:
                        raise RuntimeError(
                            "task database changed during backup; retry when the app is quiescent"
                        )
                finally:
                    check_db.unlink(missing_ok=True)
        if db.exists():
            manifest["files"]["tasks.db"] = _sha256(stage / "tasks.db")
        for file in sorted((stage / "traces").rglob("*")):
            if file.is_file():
                manifest["files"][str(file.relative_to(stage))] = _sha256(file)
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        with tarfile.open(archive, "w:gz") as tar:
            for item in sorted(stage.iterdir()):
                tar.add(item, arcname=item.name)
    try:
        archive.chmod(0o600)
    except OSError:
        pass
    return archive


def restore(archive: pathlib.Path, artifacts: pathlib.Path, *,
            force: bool = False, dry_run: bool = False) -> dict:
    artifacts = artifacts.resolve()
    if artifacts.exists() and any(artifacts.iterdir()) and not force:
        raise RuntimeError(f"target is not empty: {artifacts}; use --force")
    with tempfile.TemporaryDirectory(prefix="yaig-restore-") as tmp:
        stage = pathlib.Path(tmp)
        with tarfile.open(archive, "r:gz") as tar:
            # Python 3.12 defaults to fully-trusted extraction; archives must
            # not be able to write outside the staging directory.
            tar.extractall(stage, filter="data")
        manifest_path = stage / "manifest.json"
        if not manifest_path.exists():
            raise RuntimeError("manifest.json missing")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for rel, expected in manifest.get("files", {}).items():
            file = stage / rel
            if not file.is_file():
                raise RuntimeError(f"archive member missing: {rel}")
            if _sha256(file) != expected:
                raise RuntimeError(f"checksum mismatch: {rel}")
        if dry_run:
            return {"ok": True, "dry_run": True, "files": len(manifest.get("files", {}))}
        artifacts.mkdir(parents=True, exist_ok=True)
        if (stage / "tasks.db").exists():
            shutil.copy2(stage / "tasks.db", artifacts / "tasks.db")
        if (stage / "traces").exists():
            target = artifacts / "traces"
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(stage / "traces", target)
        else:
            # A backup without traces is an exact snapshot of "no traces"; do
            # not leave pre-existing trace files from the target directory.
            stale = artifacts / "traces"
            if stale.exists():
                shutil.rmtree(stale)
        return {"ok": True, "dry_run": False, "files": len(manifest.get("files", {}))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backup/restore YAI Guard state")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--artifacts", default="artifacts")
    b.add_argument("--out", default="backups")
    b.add_argument("--allow-active", action="store_true")
    r = sub.add_parser("restore")
    r.add_argument("archive")
    r.add_argument("--artifacts", default="artifacts")
    r.add_argument("--force", action="store_true")
    r.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            path = backup(pathlib.Path(args.artifacts), pathlib.Path(args.out),
                          allow_active=args.allow_active)
            print(json.dumps({"ok": True, "archive": str(path),
                              "size": path.stat().st_size}, indent=2))
        else:
            result = restore(pathlib.Path(args.archive), pathlib.Path(args.artifacts),
                             force=args.force, dry_run=args.dry_run)
            print(json.dumps(result, indent=2))
    except Exception as exc:  # noqa: BLE001 - operator tool should print a clean error
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

