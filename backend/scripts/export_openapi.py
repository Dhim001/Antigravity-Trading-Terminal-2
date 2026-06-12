#!/usr/bin/env python3
"""Write OpenAPI JSON to stdout or a file."""

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.api.openapi import build_openapi_spec  # noqa: E402


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    spec = build_openapi_spec()
    if out:
        out.write_text(json.dumps(spec, indent=2), encoding="utf-8")
        print(f"Wrote {out}")
    else:
        print(json.dumps(spec, indent=2))
