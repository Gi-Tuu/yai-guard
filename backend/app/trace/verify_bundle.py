"""独立验证器：python -m app.trace.verify_bundle <bundle.json> [--hmac-key KEY|--hmac-key-file FILE] [--public-key-file FILE] [--require-signature]"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .bundle import verify_bundle_dict


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a yai-guard-evidence/v1 bundle offline.")
    parser.add_argument("bundle", help="path to evidence bundle JSON")
    parser.add_argument("--hmac-key", default=None, help="optional anchor HMAC key")
    parser.add_argument("--hmac-key-file", default=None,
                        help="read the anchor HMAC key from a local file")
    parser.add_argument("--public-key-file", default=None,
                        help="read the Ed25519 public key (PEM or base64 raw)")
    parser.add_argument(
        "--require-signature",
        action="store_true",
        help="fail unless the anchor signature is verified (status VERIFIED)",
    )
    args = parser.parse_args(argv)
    if args.hmac_key and args.hmac_key_file:
        parser.error("use --hmac-key or --hmac-key-file, not both")
    hmac_key = args.hmac_key
    if args.hmac_key_file:
        hmac_key = Path(args.hmac_key_file).read_text(encoding="utf-8").strip()
        if not hmac_key:
            parser.error("HMAC key file is empty")

    public_key = None
    if args.public_key_file:
        from .signing import load_public_key

        try:
            public_key = load_public_key(args.public_key_file)
        except Exception as exc:  # noqa: BLE001
            parser.error(f"cannot load public key: {exc}")

    data = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    result = verify_bundle_dict(data, hmac_key=hmac_key, public_key=public_key)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.require_signature and result["status"] != "VERIFIED":
        return 1
    return {"VERIFIED": 0, "CONTENT_VERIFIED": 0, "FAILED": 1, "INCOMPLETE": 2}.get(
        result["status"], 3
    )


if __name__ == "__main__":
    sys.exit(main())
