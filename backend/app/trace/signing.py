"""Ed25519 evidence signatures (v2), with HMAC v1 kept for compatibility.

The signing body is the anchor without ``hmac`` and ``signature_v2``. The
signature container carries the raw public key so an offline verifier can see
which key signed it, while `--public-key-file` still lets a verifier pin an
out-of-band key instead of trusting the embedded one.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _read(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def load_private_key(source: str | Path | bytes) -> Ed25519PrivateKey:
    data = source if isinstance(source, bytes) else _read(source)
    text = data.strip()
    if text.startswith(b"-----BEGIN"):
        key = serialization.load_pem_private_key(text, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("not an Ed25519 private key")
        return key
    raw = base64.b64decode(text, validate=True)
    if len(raw) != 32:
        raise ValueError("raw Ed25519 private key must be 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(raw)


def load_public_key(source: str | Path | bytes) -> Ed25519PublicKey:
    data = source if isinstance(source, bytes) else _read(source)
    text = data.strip()
    if text.startswith(b"-----BEGIN"):
        key = serialization.load_pem_public_key(text)
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("not an Ed25519 public key")
        return key
    raw = base64.b64decode(text, validate=True)
    if len(raw) != 32:
        raise ValueError("raw Ed25519 public key must be 32 bytes")
    return Ed25519PublicKey.from_public_bytes(raw)


def public_key_b64(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def anchor_signing_body(anchor: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in anchor.items() if k not in ("hmac", "signature_v2")}


def sign_anchor(anchor: dict[str, Any], private_key: Ed25519PrivateKey) -> dict[str, Any]:
    body = canonical_json(anchor_signing_body(anchor)).encode("utf-8")
    signature = private_key.sign(body)
    return {
        "alg": "ed25519",
        "public_key": public_key_b64(private_key.public_key()),
        "sig": base64.b64encode(signature).decode("ascii"),
    }


def verify_anchor_signature(
    anchor: dict[str, Any], public_key: Ed25519PublicKey | None = None
) -> bool:
    container = anchor.get("signature_v2")
    if not isinstance(container, dict) or container.get("alg") != "ed25519":
        return False
    try:
        sig = base64.b64decode(container["sig"], validate=True)
        if public_key is None:
            raw = base64.b64decode(container["public_key"], validate=True)
            public_key = Ed25519PublicKey.from_public_bytes(raw)
        public_key.verify(sig, canonical_json(anchor_signing_body(anchor)).encode("utf-8"))
        return True
    except Exception:  # noqa: BLE001 - invalid encoding/signature is a verification failure
        return False


def embedded_public_key(anchor: dict[str, Any]) -> Ed25519PublicKey | None:
    container = anchor.get("signature_v2")
    if not isinstance(container, dict):
        return None
    try:
        raw = base64.b64decode(container.get("public_key", ""), validate=True)
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception:  # noqa: BLE001
        return None
