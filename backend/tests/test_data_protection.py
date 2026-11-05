"""Field-level encryption for task prompt/summary/error at rest."""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app import data_protection  # noqa: E402
from app.tasks.store import TaskStore  # noqa: E402
from app.tasks.models import TaskStatus  # noqa: E402


class DataProtectionTest(unittest.TestCase):
    def test_key_rotation_reencrypts_existing_fields(self) -> None:
        from cryptography.fernet import Fernet

        root = pathlib.Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location(
            "rotate_data_key_under_test", root / "scripts" / "rotate_data_key.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old_key = Fernet.generate_key().decode("ascii")
        new_key = Fernet.generate_key().decode("ascii")
        data_protection.configure(old_key)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db = pathlib.Path(tmp) / "tasks.db"
                store = TaskStore(db)
                try:
                    store.create_task(task_id="tk", trace_id="tr", owner="u",
                                      prompt="secret prompt")
                    store.transition("tk", TaskStatus.RUNNING)
                    store.finish("tk", TaskStatus.COMPLETED, summary="secret summary")
                finally:
                    store.close()
                counts = module.rotate(db, old_key, new_key)
                self.assertEqual(counts["tasks"], 1)
                data_protection.configure(new_key)
                store = TaskStore(db)
                try:
                    record = store.get_task("tk")
                    self.assertEqual(record.prompt, "secret prompt")
                    self.assertEqual(record.summary, "secret summary")
                finally:
                    store.close()
        finally:
            data_protection.configure(None)

    def test_encrypted_task_text_roundtrip_and_raw_storage(self) -> None:
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode("ascii")
        data_protection.configure(key)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                store = TaskStore(pathlib.Path(tmp) / "tasks.db")
                try:
                    store.create_task(task_id="tk", trace_id="tr", owner="u",
                                      prompt="secret task prompt")
                    record = store.get_task("tk")
                    self.assertEqual(record.prompt, "secret task prompt")
                    self.assertIsNotNone(store.transition("tk", TaskStatus.RUNNING))
                    store.finish("tk", TaskStatus.COMPLETED,
                                 summary="secret summary")
                    raw = store._conn.execute(
                        "SELECT prompt, summary FROM tasks WHERE id='tk'"
                    ).fetchone()
                    self.assertTrue(raw["prompt"].startswith("enc:v1:"))
                    self.assertTrue(raw["summary"].startswith("enc:v1:"))
                    self.assertNotIn("secret", raw["prompt"])
                    self.assertNotIn("secret", raw["summary"])
                finally:
                    store.close()
        finally:
            data_protection.configure(None)

    def test_invalid_key_fails_closed(self) -> None:
        with self.assertRaises(data_protection.DataProtectionError):
            data_protection.configure("not-a-fernet-key")
        data_protection.configure(None)
