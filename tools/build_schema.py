#!/usr/bin/env python3
"""Regenerate schemas/envelope.schema.json from the pydantic models.

The models are the wire truth; the committed schema is generated from them
and a test keeps the two identical. Edit doe_mcp/core/envelope.py, then run
this.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from doe_mcp.core.envelope import Envelope  # noqa: E402

out = ROOT / "schemas" / "envelope.schema.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(Envelope.wire_schema(), indent=2,
                          sort_keys=False) + "\n")
print(f"wrote {out}")
