from __future__ import annotations

import csv
import json
import re
from pathlib import Path

status_path = Path("docs/latest.json")
csv_path = Path("docs/latest.csv")
if not status_path.exists():
    raise SystemExit("docs/latest.json was not created")

status = json.loads(status_path.read_text(encoding="utf-8"))
expected_status_fields = {
    "status": "success",
    "schema_version": "1.2",
    "required_column_check": "success",
    "numeric_validation_status": "success",
    "price_adjustment_validation_status": "success",
}
for field, expected in expected_status_fields.items():
    actual = status.get(field)
    if actual != expected:
        raise SystemExit(f"Invalid {field}: expected {expected!r}, got {actual!r}")

if status.get("config_version") != "2026-07-mispricing-v1":
    raise SystemExit(
        "Invalid config_version: expected '2026-07-mispricing-v1', "
        f"got {status.get('config_version')!r}"
    )

config_hash = status.get("config_hash")
if not isinstance(config_hash, str) or re.fullmatch(r"[0-9a-f]{64}", config_hash) is None:
    raise SystemExit("config_hash is not a 64-character lowercase SHA-256 hex digest")

for field in ("universe_distribution", "sector_distribution"):
    value = status.get(field)
    if not isinstance(value, dict) or not value:
        raise SystemExit(f"{field} must be a non-empty object")

if not csv_path.exists():
    raise SystemExit("docs/latest.csv was not created")

required_new_columns = {
    "return_63d",
    "return_126d",
    "spy_return_63d",
    "spy_return_126d",
    "spy_relative_63d",
    "spy_relative_126d",
    "sector_etf_return_63d",
    "sector_etf_return_126d",
    "sector_relative_63d",
    "sector_relative_126d",
    "max_daily_move_date_21d",
    "max_daily_move_signed_21d",
    "max_1d_share_of_abs_move_21d",
    "directional_efficiency_21d",
    "post_max_move_return_5d",
    "post_max_move_return_10d",
}
with csv_path.open("r", encoding="utf-8", newline="") as handle:
    reader = csv.DictReader(handle)
    columns = set(reader.fieldnames or [])
    row_count = sum(1 for _ in reader)

missing_columns = sorted(required_new_columns - columns)
if missing_columns:
    raise SystemExit(f"docs/latest.csv is missing required columns: {missing_columns}")

if status.get("row_count") != row_count:
    raise SystemExit(
        "row_count mismatch: "
        f"latest.json={status.get('row_count')!r}, latest.csv={row_count}"
    )

print(f"Screening status: success ({row_count} rows, schema 1.2)")
