#!/usr/bin/env python
"""Re-encrypt task prompt/summary/error from one Fernet key to another.

Keys are read from files, never from argv, so they do not appear in shell
history. The script does not print key material. Back up the DB first.
"""
from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys

PREFIX = "enc:v1:"


def _read_key(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("TASK_DATA_KEY="):
        text = text.split("=", 1)[1].strip()
    if not text:
        raise RuntimeError(f"empty key file: {path}")
    return text


def _rotate_value(value: str | None, old, new) -> str | None:
    if value is None:
        return None
    if value.startswith(PREFIX):
        plain = old.decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    else:
        plain = value
    return PREFIX + new.encrypt(plain.encode("utf-8")).decode("ascii")


def rotate(db_path: pathlib.Path, old_key: str, new_key: str,
           *, dry_run: bool = False) -> dict[str, int]:
    from cryptography.fernet import Fernet

    old = Fernet(old_key.encode("ascii"))
    new = Fernet(new_key.encode("ascii"))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    counts = {"tasks": 0, "fields": 0}
    try:
        rows = conn.execute("SELECT id, prompt, summary, error FROM tasks").fetchall()
        with conn:
            for row in rows:
                changed = False
                values = {}
                for field in ("prompt", "summary", "error"):
                    value = row[field]
                    if value is None:
                        continue
                    try:
                        rotated = _rotate_value(value, old, new)
                    except Exception as exc:  # noqa: BLE001
                        raise RuntimeError(
                            f"cannot decrypt task {row['id']} field {field}: "
                            f"{type(exc).__name__}"
                        ) from exc
                    if rotated != value:
                        values[field] = rotated
                        changed = True
                if changed:
                    counts["tasks"] += 1
                    counts["fields"] += len(values)
                    if not dry_run:
                        assignments = ", ".join(f"{k} = ?" for k in values)
                        conn.execute(
                            f"UPDATE tasks SET {assignments} WHERE id = ?",
                            (*values.values(), row["id"]),
                        )
        return counts
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rotate TASK_DATA_KEY")
    parser.add_argument("--db", required=True)
    parser.add_argument("--old-key-file", required=True)
    parser.add_argument("--new-key-file", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        counts = rotate(
            pathlib.Path(args.db),
            _read_key(pathlib.Path(args.old_key_file)),
            _read_key(pathlib.Path(args.new_key_file)),
            dry_run=args.dry_run,
        )
        print({"ok": True, "dry_run": args.dry_run, **counts})
    except Exception as exc:  # noqa: BLE001
        print({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
