"""Publish the versioned OpenAPI contract and detect uncommitted contract drift."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUTPUT = ROOT / "apps" / "api" / "openapi" / "v1.json"


def rendered_contract() -> str:
    from apps.api.app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when the committed contract is stale")
    args = parser.parse_args()
    content = rendered_contract()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != content:
            print("OpenAPI contract is stale. Run: .venv\\Scripts\\python.exe scripts\\generate-openapi.py")
            return 1
        print(f"OpenAPI contract is current: {OUTPUT.relative_to(ROOT)}")
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
