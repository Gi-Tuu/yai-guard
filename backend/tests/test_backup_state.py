"""Backup/restore contract test (no external services)."""
from __future__ import annotations

import json
import importlib.util
import io
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


class BackupStateTest(unittest.TestCase):
    def _backup_module(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location(
            "backup_state_under_test", root / "scripts" / "backup_state.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_quiesce_mode_creates_and_removes_marker(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = pathlib.Path(tmp) / "artifacts"
            artifacts.mkdir()
            conn = sqlite3.connect(artifacts / "tasks.db")
            conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT)")
            conn.commit()
            conn.close()
            result = subprocess.run(
                [sys.executable, str(root / "scripts" / "backup_state.py"),
                 "backup", "--artifacts", str(artifacts),
                 "--out", str(pathlib.Path(tmp) / "backups"), "--quiesce"],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((artifacts / "quiesce").exists())

    def test_backup_rejects_state_change_during_snapshot(self) -> None:
        module = self._backup_module()
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = pathlib.Path(tmp) / "artifacts"
            (artifacts / "traces").mkdir(parents=True)
            (artifacts / "traces" / "tr.jsonl").write_text("{}\n", encoding="utf-8")
            with mock.patch.object(module, "_tree_fingerprint",
                                   side_effect=[{"tr.jsonl": (1, 1)},
                                                {"tr.jsonl": (2, 2)}]):
                with self.assertRaises(RuntimeError):
                    module.backup(artifacts, pathlib.Path(tmp) / "backups")

    def test_backup_and_restore_roundtrip(self) -> None:
        root = pathlib.Path(__file__).resolve().parents[2]
        script = root / "scripts" / "backup_state.py"
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            artifacts = base / "artifacts"
            traces = artifacts / "traces"
            traces.mkdir(parents=True)
            (traces / "tr_demo.jsonl").write_text('{"event":"task.created"}\n',
                                                   encoding="utf-8")
            conn = sqlite3.connect(artifacts / "tasks.db")
            conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT)")
            conn.execute("INSERT INTO tasks VALUES ('tk_demo','completed')")
            conn.commit()
            conn.close()

            backup = subprocess.run(
                [sys.executable, str(script), "backup",
                 "--artifacts", str(artifacts), "--out", str(base / "backups")],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(backup.returncode, 0, backup.stderr)
            archive = pathlib.Path(json.loads(backup.stdout)["archive"])
            self.assertTrue(archive.exists())

            restored = base / "restored"
            dry = subprocess.run(
                [sys.executable, str(script), "restore", str(archive),
                 "--artifacts", str(restored), "--dry-run"],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertFalse(restored.exists())

            run = subprocess.run(
                [sys.executable, str(script), "restore", str(archive),
                 "--artifacts", str(restored)],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            conn = sqlite3.connect(restored / "tasks.db")
            try:
                self.assertEqual(
                    conn.execute("SELECT status FROM tasks WHERE id='tk_demo'").fetchone(),
                    ("completed",),
                )
            finally:
                conn.close()
            self.assertEqual(
                (restored / "traces" / "tr_demo.jsonl").read_text(encoding="utf-8"),
                '{"event":"task.created"}\n',
            )

    def test_restore_rejects_path_traversal(self) -> None:
        import tarfile

        root = pathlib.Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            base = pathlib.Path(tmp)
            archive = base / "evil.tar.gz"
            manifest = json.dumps({"schema": "yai-guard-backup/v1", "files": {}})
            with tarfile.open(archive, "w:gz") as tar:
                data = manifest.encode("utf-8")
                info = tarfile.TarInfo("manifest.json")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
                evil = b"escaped"
                info = tarfile.TarInfo("../escaped.txt")
                info.size = len(evil)
                tar.addfile(info, io.BytesIO(evil))
            result = subprocess.run(
                [sys.executable, str(root / "scripts" / "backup_state.py"),
                 "restore", str(archive), "--artifacts", str(base / "target"), "--force"],
                capture_output=True, text=True, timeout=60,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((base / "escaped.txt").exists())


if __name__ == "__main__":
    unittest.main()
