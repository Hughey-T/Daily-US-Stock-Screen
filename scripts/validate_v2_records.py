#!/usr/bin/env python3
"""Validate immutable schema-2.0 JSON records and their pinned snapshot."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.integrity import (ACTION_CLASS_MAPPING_VERSION, IntegrityError,
    display_action_class, make_prediction_id, neutral_threshold,
    validate_confidence, validate_information_timeline, validate_valuation,
    verify_snapshot)

def validate_prediction(path: Path, root: Path = ROOT) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("prediction_schema_version") != "2.0":
        raise IntegrityError("unsupported prediction_schema_version")
    snapshot = verify_snapshot(root / data["source_snapshot_path"])
    if snapshot.get("snapshot_id") != data["source_snapshot_id"]:
        raise IntegrityError("source snapshot ID mismatch")
    assessments = data.get("candidate_assessments", [])
    forecasts = data.get("horizon_forecasts", [])
    by_route, entity_classes = {}, {}
    for row in assessments:
        route = row["route_candidate_id"]
        if route in by_route:
            raise IntegrityError("duplicate route_candidate_id")
        expected = display_action_class(row)
        if row.get("display_action_class") != expected or row.get("action_class_mapping_version") != ACTION_CLASS_MAPPING_VERSION:
            raise IntegrityError("display action class mismatch")
        validate_valuation(row["valuation_status"], row.get("estimated_fundamental_value_change_low"),
                           row.get("estimated_fundamental_value_change_base"),
                           row.get("estimated_fundamental_value_change_high"))
        entity_classes.setdefault(row["entity_id"], set()).add(expected)
        by_route[route] = row
    if any(len(values) != 1 for values in entity_classes.values()):
        raise IntegrityError("entity has multiple primary display classifications")
    ids = set()
    for row in forecasts:
        if row["route_candidate_id"] not in by_route:
            raise IntegrityError("forecast has no candidate assessment")
        validate_information_timeline(data["information_cutoff_at"], row["first_tradable_at"], row["entry_price_timestamp"])
        validate_confidence(row["forecast_confidence"], float(row["forecast_probability"]))
        if float(row["absolute_neutral_threshold"]) != neutral_threshold(int(row["horizon"]), "absolute") or float(row["relative_neutral_threshold"]) != neutral_threshold(int(row["horizon"]), "relative"):
            raise IntegrityError("neutral threshold is not pre-registered")
        expected = make_prediction_id(row["route_candidate_id"], int(row["horizon"]), data["prediction_run_id"], data["prompt_version"], row["entry_price_timestamp"])
        if row["prediction_id"] != expected or expected in ids:
            raise IntegrityError("prediction_id is invalid or duplicated")
        ids.add(expected)
    finals = {a["route_candidate_id"] for a in assessments if a["record_group"] == "forecast"}
    comparisons = {a.get("comparison_for") for a in assessments if a["record_group"] == "comparison_only"}
    if finals - comparisons:
        raise IntegrityError("matched comparison record is missing")
    return data

def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("paths", nargs="*")
    args=parser.parse_args(); paths=[Path(p) for p in args.paths] or sorted(
        p for p in (ROOT/'docs/predictions').glob('*.json') if p.name != 'index.json'
    )
    try:
        for path in paths: validate_prediction(path)
    except (KeyError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"schema 2.0 validation failed: {exc}", file=sys.stderr); raise SystemExit(1)
    print(f"schema 2.0 validation succeeded: {len(paths)} immutable bundles")
if __name__ == '__main__': main()
