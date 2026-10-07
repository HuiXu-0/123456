"""生成或校验 backend/contracts/openapi-v1.snapshot.json。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.openapi_contract import build_openapi_snapshot  # noqa: E402
from app.main import app  # noqa: E402

TARGET = Path(__file__).resolve().parents[1] / "contracts" / "openapi-v1.snapshot.json"


def render() -> str:
    return json.dumps(build_openapi_snapshot(app.openapi()), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    args = parser.parse_args()
    current = render()
    if args.check:
        if not TARGET.exists() or TARGET.read_text(encoding="utf-8") != current:
            print(f"OpenAPI snapshot is stale: {TARGET}", file=sys.stderr)
            return 1
        print(f"OpenAPI snapshot is current: {TARGET}")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(current, encoding="utf-8", newline="\n")
    print(f"Wrote OpenAPI snapshot: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
