"""独立验证器：python -m app.trace.verify_bundle <bundle.json> [--hmac-key KEY]"""
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
    args = parser.parse_args(argv)

    data = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
    result = verify_bundle_dict(data, hmac_key=args.hmac_key)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return {"VERIFIED": 0, "FAILED": 1, "INCOMPLETE": 2}.get(result["status"], 3)


if __name__ == "__main__":
    sys.exit(main())
