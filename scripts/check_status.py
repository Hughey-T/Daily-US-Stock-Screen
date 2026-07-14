from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path

status_path = Path("docs/latest.json")
csv_path = Path("docs/latest.csv")
if not status_path.exists():
    raise SystemExit("docs/latest.json was not created")

raw_status = status_path.read_text(encoding="utf-8")
if re.search(r"(?<![A-Za-z])(NaN|Infinity)(?![A-Za-z])", raw_status):
    raise SystemExit("docs/latest.json contains NaN or Infinity")
status = json.loads(raw_status)
expected_status_fields = {
    "status": "success",
    "schema_version": "1.3",
    "required_column_check": "success",
    "numeric_validation_status": "success",
    "price_adjustment_validation_status": "success",
}
for field, expected in expected_status_fields.items():
    actual = status.get(field)
    if actual != expected:
        raise SystemExit(f"Invalid {field}: expected {expected!r}, got {actual!r}")

if status.get("config_version") != "2026-07-mispricing-v2":
    raise SystemExit(
        "Invalid config_version: expected '2026-07-mispricing-v2', "
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

if status.get("quiet_drift_enabled"):
    if status.get("quiet_drift_status") != "success":
        raise SystemExit("quiet_drift_status must be success when enabled")
    if status.get("quiet_drift_required_column_check") != "success":
        raise SystemExit("quiet_drift_required_column_check must be success")

    quiet_csv_value = status.get("quiet_drift_csv_file")
    quiet_archive_value = status.get("quiet_drift_archive_file")
    if not isinstance(quiet_csv_value, str) or not isinstance(quiet_archive_value, str):
        raise SystemExit("Quiet drift CSV paths are missing")
    quiet_csv_path = status_path.parent / quiet_csv_value
    quiet_archive_path = status_path.parent / quiet_archive_value
    for path in (quiet_csv_path, quiet_archive_path):
        if not path.exists():
            raise SystemExit(f"Quiet drift output does not exist: {path}")

    quiet_required_columns = {
        "rank",
        "ticker",
        "company_name",
        "sector",
        "market_cap",
        "market_data_date",
        "price",
        "return_21d",
        "return_63d",
        "return_126d",
        "spy_relative_63d",
        "spy_relative_126d",
        "sector_etf",
        "sector_relative_63d",
        "sector_relative_126d",
        "max_daily_move_63d",
        "max_gap_63d",
        "max_1d_share_of_abs_move_63d",
        "directional_efficiency_63d",
        "positive_days_63d",
        "negative_days_63d",
        "positive_day_ratio_63d",
        "volume_ratio_5d_vs_prev20d",
        "volatility_ratio_20d_vs_prev120d",
        "selection_bucket",
        "selection_reason",
        "anchor_horizon",
        "drift_direction",
        "tail_percentile",
        "tail_distance",
    }
    with quiet_csv_path.open("r", encoding="utf-8", newline="") as handle:
        quiet_reader = csv.DictReader(handle)
        quiet_columns = set(quiet_reader.fieldnames or [])
        quiet_rows = list(quiet_reader)

    missing_quiet_columns = sorted(quiet_required_columns - quiet_columns)
    if missing_quiet_columns:
        raise SystemExit(
            f"quiet_drift.csv is missing required columns: {missing_quiet_columns}"
        )
    if status.get("quiet_drift_row_count") != len(quiet_rows):
        raise SystemExit(
            "quiet_drift_row_count mismatch: "
            f"latest.json={status.get('quiet_drift_row_count')!r}, "
            f"quiet_drift.csv={len(quiet_rows)}"
        )

    tickers = [row["ticker"] for row in quiet_rows]
    if len(tickers) != len(set(tickers)):
        raise SystemExit("quiet_drift.csv contains duplicate tickers")
    expected_ranks = list(range(1, len(quiet_rows) + 1))
    try:
        actual_ranks = [int(row["rank"]) for row in quiet_rows]
    except ValueError as exc:
        raise SystemExit("quiet_drift.csv contains an invalid rank") from exc
    if actual_ranks != expected_ranks:
        raise SystemExit("quiet_drift.csv ranks are not consecutive from 1")

    market_date = status.get("market_data_date")
    if any(row["market_data_date"] != market_date for row in quiet_rows):
        raise SystemExit("quiet_drift.csv market_data_date does not match latest.json")
    if any(row["drift_direction"] not in {"up", "down"} for row in quiet_rows):
        raise SystemExit("quiet_drift.csv contains an invalid drift_direction")
    if any(row["anchor_horizon"] not in {"63d", "126d"} for row in quiet_rows):
        raise SystemExit("quiet_drift.csv contains an invalid anchor_horizon")

    for column in (
        "tail_percentile",
        "tail_distance",
        "directional_efficiency_63d",
    ):
        for row in quiet_rows:
            value = row[column]
            if value == "":
                continue
            try:
                numeric = float(value)
            except ValueError as exc:
                raise SystemExit(f"quiet_drift.csv contains invalid {column}") from exc
            if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
                raise SystemExit(f"quiet_drift.csv {column} is outside [0, 1]")

    for field in (
        "quiet_drift_selection_definition",
        "quiet_drift_thresholds",
        "quiet_drift_distribution",
    ):
        value = status.get(field)
        if not isinstance(value, dict) or not value:
            raise SystemExit(f"{field} must be a non-empty object")

print(
    f"Screening status: success ({row_count} event rows, "
    f"{status.get('quiet_drift_row_count', 0)} quiet drift rows, schema 1.3)"
)
