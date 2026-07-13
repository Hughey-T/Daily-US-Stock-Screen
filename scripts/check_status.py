from __future__ import annotations

import json
from pathlib import Path

status_path = Path("docs/latest.json")
if not status_path.exists():
    raise SystemExit("docs/latest.json was not created")

status = json.loads(status_path.read_text(encoding="utf-8"))
if status.get("status") == "failed":
    raise SystemExit(f"Screening failed: {status.get('failure_reason', 'unknown error')}")

print(f"Screening status: {status.get('status')}")
