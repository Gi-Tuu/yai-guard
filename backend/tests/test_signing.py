"""Ed25519 v2 signature tests."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from app.trace.signing import (  # noqa: E402
    load_private_key,
    load_public_key,
    sign_anchor,
    verify_anchor_signature,
)


class SigningTest(unittest.TestCase):
    def setUp(self) -> None:
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key()
        self.anchor = {
            "schema_version": "1.0",
            "trace_id": "tr_test",
            "events": 3,
            "final_hash": "sha256:" + "a" * 64,
            "sealed_at": "2026-01-01T00:00:00.000Z",
        }

    def test_sign_and_verify_with_pinned_and_embedded_key(self) -> None:
        signed = dict(self.anchor)
        signed["signature_v2"] = sign_anchor(signed, self.private)
        self.assertTrue(verify_anchor_signature(signed, self.public))
        self.assertTrue(verify_anchor_signature(signed))
        signed["events"] = 4
        self.assertFalse(verify_anchor_signature(signed, self.public))

    def test_wrong_key_fails(self) -> None:
        signed = dict(self.anchor)
        signed["signature_v2"] = sign_anchor(signed, self.private)
        other = Ed25519PrivateKey.generate().public_key()
        self.assertFalse(verify_anchor_signature(signed, other))

    def test_load_pem_and_raw_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            priv_path = pathlib.Path(tmp) / "ed25519.pem"
            pub_path = pathlib.Path(tmp) / "ed25519.pub"
            priv_path.write_bytes(self.private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            pub_path.write_bytes(self.public.public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ))
            self.assertIsNotNone(load_private_key(priv_path))
            self.assertIsNotNone(load_public_key(pub_path))


if __name__ == "__main__":
    unittest.main()
