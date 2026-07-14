from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS_DIR = ROOT / "docs" / "predictions"
VERIFICATIONS_DIR = ROOT / "docs" / "verifications"
PREDICTION_SCHEMA_VERSION = "1.1"

PREDICTION_REQUIRED_COLUMNS = (
    "prediction_schema_version",
    "prediction_id",
    "candidate_id",
    "run_id",
    "run_date",
    "market_data_date",
    "verification_horizon",
    "ticker",
    "company_name",
    "record_group",
    "prediction_applicability",
    "source_dataset",
    "source_rank",
    "selection_bucket",
    "anchor_horizon",
    "drift_direction",
    "trend_consistency",
    "entry_price",
    "entry_price_definition",
    "predicted_absolute_direction",
    "predicted_sector_relative_direction",
    "benchmark",
    "sector_etf",
    "confidence",
    "action_class",
    "thesis",
    "invalidation_condition",
    "source_snapshot",
    "prompt_version",
    "config_version",
    "config_hash",
)

PREDICTION_OPTIONAL_COLUMNS = (
    "secondary_tags",
    "thesis_fact_date",
    "thesis_source_url",
)

FUTURE_FIELD_NAMES = {
    "future_stock_return",
    "future_spy_relative_return",
    "future_sector_relative_return",
    "max_favorable_excursion",
    "max_adverse_excursion",
    "max_upside_during_period",
    "max_drawdown_during_period",
    "absolute_direction_hit",
    "sector_relative_direction_hit",
    "outcome",
    "verification_date",
    "verification_data_source",
}

VERIFICATION_REQUIRED_COLUMNS = (
    "prediction_id",
    "verification_date",
    "verification_horizon",
    "future_stock_return",
    "future_spy_relative_return",
    "future_sector_relative_return",
    "max_favorable_excursion",
    "max_adverse_excursion",
    "absolute_direction_hit",
    "sector_relative_direction_hit",
    "outcome",
    "verification_data_source",
)

VERIFICATION_OPTIONAL_COLUMNS = (
    "max_upside_during_period",
    "max_drawdown_during_period",
)

HORIZONS = {21, 63, 126, 252}
ENUMS = {
    "record_group": {
        "final_candidate",
        "nonselected_comparison",
        "unresolved_monitor",
    },
    "source_dataset": {"event_anomaly", "quiet_drift"},
    "confidence": {"high", "medium", "low"},
    "prediction_applicability": {"forecast", "comparison_only", "monitor_only"},
}
APPLICABILITY_RECORD_GROUP = {
    "forecast": "final_candidate",
    "comparison_only": "nonselected_comparison",
    "monitor_only": "unresolved_monitor",
}
ABSOLUTE_DIRECTIONS = {"up", "down", "neutral"}
SECTOR_RELATIVE_DIRECTIONS = {"outperform", "underperform", "neutral"}
ACTION_CLASSES = {"A", "B", "C", "D", "E"}
SECONDARY_TAGS_PATTERN = re.compile(r"[A-Za-z0-9_]+(?:;[A-Za-z0-9_]+)*")
HASH_PATTERN = re.compile(r"[0-9a-f]{64}")
PREDICTION_ID_PATTERN = re.compile(r"pred_[0-9a-f]{64}")
SNAPSHOT_FILE_KEYS = ("latest_json", "latest_csv", "quiet_drift_csv")


class ValidationError(ValueError):
    pass


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def make_candidate_id(
    market_data_date: str,
    source_dataset: str,
    ticker: str,
    config_hash: str,
) -> str:
    canonical = "|".join(
        (
            market_data_date.strip(),
            source_dataset.strip(),
            ticker.strip().upper(),
            config_hash.strip(),
        )
    )
    return f"cand_{_digest(canonical)}"


def make_prediction_id(candidate_id: str, verification_horizon: int) -> str:
    canonical = f"{candidate_id.strip()}|{verification_horizon}"
    return f"pred_{_digest(canonical)}"


def _read_csv(
    path: Path,
    required_columns: tuple[str, ...],
    optional_columns: tuple[str, ...] = (),
    forbidden_columns: set[str] | None = None,
) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames or []
            forbidden = sorted(set(fieldnames) & (forbidden_columns or set()))
            if forbidden:
                raise ValidationError(
                    f"{path}: post-prediction fields are forbidden: {forbidden}"
                )
            missing = [column for column in required_columns if column not in fieldnames]
            if missing:
                raise ValidationError(f"{path}: missing required columns: {missing}")
            allowed = set(required_columns) | set(optional_columns)
            unknown = sorted(set(fieldnames) - allowed)
            if unknown:
                raise ValidationError(f"{path}: unknown columns are not allowed: {unknown}")
            return [dict(row) for row in reader]
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{path}: CSV must be UTF-8") from exc


def _nonempty(row: dict[str, str], column: str, location: str) -> str:
    value = (row.get(column) or "").strip()
    if not value:
        raise ValidationError(f"{location}: {column} must not be empty")
    return value


def _date(value: str, column: str, location: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"{location}: {column} must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValidationError(f"{location}: {column} must be canonical YYYY-MM-DD")
    return value


def _integer(value: str, column: str, location: str, minimum: int | None = None) -> int:
    if not re.fullmatch(r"-?\d+", value):
        raise ValidationError(f"{location}: {column} must be an integer")
    parsed = int(value)
    if minimum is not None and parsed < minimum:
        raise ValidationError(f"{location}: {column} must be >= {minimum}")
    return parsed


def _finite_number(value: str, column: str, location: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValidationError(f"{location}: {column} must be numeric") from exc
    if not math.isfinite(parsed):
        raise ValidationError(f"{location}: {column} must be finite")
    return parsed


def _read_url(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Daily-US-Stock-Screen-validator/1.0"})
    try:
        with urlopen(request, timeout=15) as response:
            return response.read()
    except OSError as exc:
        raise ValidationError(f"source_snapshot is not retrievable: {url}: {exc}") from exc


def _safe_local_path(repo_root: Path, value: str) -> Path:
    candidate = (repo_root / Path(value)).resolve()
    root = repo_root.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValidationError(f"source_snapshot escapes repository root: {value}")
    return candidate


def validate_source_snapshot(
    source_snapshot: str,
    market_data_date: str,
    repo_root: Path = ROOT,
) -> None:
    parsed_url = urlparse(source_snapshot)
    is_remote = parsed_url.scheme in {"http", "https"}
    if parsed_url.scheme and not is_remote:
        raise ValidationError("source_snapshot must be a repository path or HTTP(S) URL")

    if is_remote:
        manifest_bytes = _read_url(source_snapshot)
        manifest_base: str | Path = source_snapshot
    else:
        manifest_path = _safe_local_path(repo_root, source_snapshot)
        if not manifest_path.is_file():
            raise ValidationError(f"source_snapshot does not exist: {source_snapshot}")
        manifest_bytes = manifest_path.read_bytes()
        manifest_base = manifest_path

    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError(
            f"source_snapshot is not valid UTF-8 JSON: {source_snapshot}"
        ) from exc

    if manifest.get("market_data_date") != market_data_date:
        raise ValidationError(
            "source_snapshot market_data_date does not match prediction: "
            f"{manifest.get('market_data_date')!r} != {market_data_date!r}"
        )
    files = manifest.get("files")
    if not isinstance(files, dict):
        raise ValidationError("source_snapshot manifest must contain a files object")

    for key in SNAPSHOT_FILE_KEYS:
        metadata = files.get(key)
        if not isinstance(metadata, dict):
            raise ValidationError(f"source_snapshot manifest is missing files.{key}")
        resource = metadata.get("path")
        expected_hash = metadata.get("sha256")
        if not isinstance(resource, str) or not resource:
            raise ValidationError(f"source_snapshot files.{key}.path is invalid")
        if (
            not isinstance(expected_hash, str)
            or HASH_PATTERN.fullmatch(expected_hash) is None
        ):
            raise ValidationError(f"source_snapshot files.{key}.sha256 is invalid")

        if is_remote:
            resource_bytes = _read_url(urljoin(str(manifest_base), resource))
        else:
            assert isinstance(manifest_base, Path)
            resource_path = (manifest_base.parent / resource).resolve()
            if manifest_base.parent.resolve() not in (
                resource_path,
                *resource_path.parents,
            ):
                raise ValidationError(
                    "source_snapshot resource escapes snapshot directory: "
                    f"{resource}"
                )
            if not resource_path.is_file():
                raise ValidationError(
                    f"source_snapshot resource does not exist: {resource_path}"
                )
            resource_bytes = resource_path.read_bytes()
        actual_hash = hashlib.sha256(resource_bytes).hexdigest()
        if actual_hash != expected_hash:
            raise ValidationError(
                f"source_snapshot hash mismatch for {key}: {actual_hash} != {expected_hash}"
            )


def count_future_field_violations(predictions_dir: Path = PREDICTIONS_DIR) -> int:
    violation_count = 0
    for path in sorted(predictions_dir.glob("*.csv")):
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            fieldnames = csv.DictReader(handle).fieldnames or []
        violation_count += len(set(fieldnames) & FUTURE_FIELD_NAMES)
    return violation_count


def action_class_conflicts(
    records: dict[str, dict[str, str]],
) -> dict[tuple[str, str], set[str]]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for record in records.values():
        if record["record_group"] != "final_candidate":
            continue
        key = (record["candidate_id"], record["run_id"])
        grouped.setdefault(key, set()).add(record["action_class"])
    return {key: classes for key, classes in grouped.items() if len(classes) > 1}


def load_prediction_records(
    predictions_dir: Path = PREDICTIONS_DIR,
    repo_root: Path = ROOT,
    validate_snapshots: bool = True,
) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    validated_snapshots: set[tuple[str, str]] = set()
    for path in sorted(predictions_dir.glob("*.csv")):
        rows = _read_csv(
            path,
            PREDICTION_REQUIRED_COLUMNS,
            PREDICTION_OPTIONAL_COLUMNS,
            FUTURE_FIELD_NAMES,
        )
        for line_number, row in enumerate(rows, start=2):
            location = f"{path}:{line_number}"
            values = {
                column: (row.get(column) or "").strip()
                for column in (*PREDICTION_REQUIRED_COLUMNS, *PREDICTION_OPTIONAL_COLUMNS)
            }
            conditionally_empty = {
                "predicted_absolute_direction",
                "predicted_sector_relative_direction",
                "action_class",
            }
            for column in PREDICTION_REQUIRED_COLUMNS:
                if column not in conditionally_empty:
                    _nonempty(values, column, location)
            if values["prediction_schema_version"] != PREDICTION_SCHEMA_VERSION:
                raise ValidationError(
                    f"{location}: prediction_schema_version must be "
                    f"{PREDICTION_SCHEMA_VERSION}"
                )
            _date(values["run_date"], "run_date", location)
            _date(values["market_data_date"], "market_data_date", location)
            horizon = _integer(values["verification_horizon"], "verification_horizon", location)
            if horizon not in HORIZONS:
                raise ValidationError(
                    f"{location}: verification_horizon must be one of {sorted(HORIZONS)}"
                )
            _integer(values["source_rank"], "source_rank", location, minimum=1)
            entry_price = _finite_number(values["entry_price"], "entry_price", location)
            if entry_price <= 0:
                raise ValidationError(f"{location}: entry_price must be positive")
            if HASH_PATTERN.fullmatch(values["config_hash"]) is None:
                raise ValidationError(f"{location}: config_hash must be a lowercase SHA-256 digest")
            for column, allowed in ENUMS.items():
                if values[column] not in allowed:
                    raise ValidationError(
                        f"{location}: {column} must be one of {sorted(allowed)}"
                    )

            applicability = values["prediction_applicability"]
            expected_group = APPLICABILITY_RECORD_GROUP[applicability]
            if values["record_group"] != expected_group:
                raise ValidationError(
                    f"{location}: {applicability} requires record_group={expected_group}"
                )
            absolute_direction = values["predicted_absolute_direction"]
            sector_direction = values["predicted_sector_relative_direction"]
            action_class = values["action_class"]
            if applicability == "forecast":
                if absolute_direction not in ABSOLUTE_DIRECTIONS:
                    raise ValidationError(
                        f"{location}: forecast requires predicted_absolute_direction"
                    )
                if sector_direction not in SECTOR_RELATIVE_DIRECTIONS:
                    raise ValidationError(
                        f"{location}: forecast requires "
                        "predicted_sector_relative_direction"
                    )
                if action_class not in ACTION_CLASSES:
                    raise ValidationError(
                        f"{location}: forecast action_class must be one of "
                        f"{sorted(ACTION_CLASSES)}"
                    )
            else:
                if absolute_direction:
                    raise ValidationError(
                        f"{location}: {applicability} requires an empty "
                        "predicted_absolute_direction"
                    )
                if sector_direction:
                    raise ValidationError(
                        f"{location}: {applicability} requires an empty "
                        "predicted_sector_relative_direction"
                    )
                if action_class and action_class not in ACTION_CLASSES:
                    raise ValidationError(
                        f"{location}: action_class must be empty or one of "
                        f"{sorted(ACTION_CLASSES)}"
                    )

            secondary_tags = values["secondary_tags"]
            if secondary_tags and SECONDARY_TAGS_PATTERN.fullmatch(secondary_tags) is None:
                raise ValidationError(
                    f"{location}: secondary_tags must be semicolon-separated tags"
                )
            thesis_fact_date = values["thesis_fact_date"]
            if thesis_fact_date:
                _date(thesis_fact_date, "thesis_fact_date", location)
                if date.fromisoformat(thesis_fact_date) > date.fromisoformat(
                    values["market_data_date"]
                ):
                    raise ValidationError(
                        f"{location}: thesis_fact_date must not be after market_data_date"
                    )
            thesis_source_url = values["thesis_source_url"]
            if thesis_source_url:
                thesis_url = urlparse(thesis_source_url)
                if thesis_url.scheme not in {"http", "https"} or not thesis_url.netloc:
                    raise ValidationError(
                        f"{location}: thesis_source_url must be an HTTP(S) URL"
                    )

            expected_candidate_id = make_candidate_id(
                values["market_data_date"],
                values["source_dataset"],
                values["ticker"],
                values["config_hash"],
            )
            if values["candidate_id"] != expected_candidate_id:
                raise ValidationError(
                    f"{location}: candidate_id is not deterministic; "
                    f"expected {expected_candidate_id}"
                )
            expected_prediction_id = make_prediction_id(expected_candidate_id, horizon)
            if values["prediction_id"] != expected_prediction_id:
                raise ValidationError(
                    f"{location}: prediction_id is not deterministic; "
                    f"expected {expected_prediction_id}"
                )
            if values["prediction_id"] in records:
                previous = records[values["prediction_id"]]["_location"]
                raise ValidationError(
                    f"{location}: duplicate prediction_id also present at {previous}"
                )
            if validate_snapshots:
                snapshot_key = (values["source_snapshot"], values["market_data_date"])
                if snapshot_key not in validated_snapshots:
                    validate_source_snapshot(*snapshot_key, repo_root=repo_root)
                    validated_snapshots.add(snapshot_key)
            records[values["prediction_id"]] = {**values, "_file": str(path), "_location": location}
    conflicts = action_class_conflicts(records)
    if conflicts:
        rendered = "; ".join(
            f"candidate_id={candidate_id}, run_id={run_id}, classes={sorted(classes)}"
            for (candidate_id, run_id), classes in sorted(conflicts.items())
        )
        raise ValidationError(
            "final candidates have conflicting action_class values: " + rendered
        )
    return records


def load_verification_records(
    predictions: dict[str, dict[str, str]],
    verifications_dir: Path = VERIFICATIONS_DIR,
) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    numeric_columns = (
        "future_stock_return",
        "future_spy_relative_return",
        "future_sector_relative_return",
        "max_favorable_excursion",
        "max_adverse_excursion",
    )
    for path in sorted(verifications_dir.glob("*.csv")):
        rows = _read_csv(
            path,
            VERIFICATION_REQUIRED_COLUMNS,
            VERIFICATION_OPTIONAL_COLUMNS,
        )
        for line_number, row in enumerate(rows, start=2):
            location = f"{path}:{line_number}"
            values = {
                column: (row.get(column) or "").strip()
                for column in (*VERIFICATION_REQUIRED_COLUMNS, *VERIFICATION_OPTIONAL_COLUMNS)
            }
            conditionally_empty = {
                "absolute_direction_hit",
                "sector_relative_direction_hit",
                "outcome",
            }
            for column in VERIFICATION_REQUIRED_COLUMNS:
                if column not in conditionally_empty:
                    _nonempty(values, column, location)
            prediction_id = values["prediction_id"]
            if PREDICTION_ID_PATTERN.fullmatch(prediction_id) is None:
                raise ValidationError(f"{location}: prediction_id has an invalid format")
            if prediction_id not in predictions:
                raise ValidationError(f"{location}: prediction_id has no matching prediction")
            if prediction_id in records:
                raise ValidationError(f"{location}: duplicate verification for {prediction_id}")
            _date(values["verification_date"], "verification_date", location)
            horizon = _integer(values["verification_horizon"], "verification_horizon", location)
            if horizon not in HORIZONS:
                raise ValidationError(
                    f"{location}: verification_horizon must be one of {sorted(HORIZONS)}"
                )
            if horizon != int(predictions[prediction_id]["verification_horizon"]):
                raise ValidationError(f"{location}: verification_horizon does not match prediction")
            for column in numeric_columns:
                _finite_number(values[column], column, location)
            for column in VERIFICATION_OPTIONAL_COLUMNS:
                if values[column]:
                    _finite_number(values[column], column, location)

            applicability = predictions[prediction_id]["prediction_applicability"]
            hit_columns = (
                "absolute_direction_hit",
                "sector_relative_direction_hit",
            )
            if applicability == "forecast":
                for column in hit_columns:
                    if values[column].lower() not in {"true", "false"}:
                        raise ValidationError(
                            f"{location}: forecast {column} must be true or false"
                        )
                if not values["outcome"]:
                    raise ValidationError(f"{location}: forecast outcome must not be empty")
            else:
                for column in (*hit_columns, "outcome"):
                    if values[column]:
                        raise ValidationError(
                            f"{location}: {applicability} requires an empty {column}"
                        )
            records[prediction_id] = {**values, "_file": str(path), "_location": location}
    return records


def join_prediction_verification_records(
    predictions: dict[str, dict[str, str]],
    verifications: dict[str, dict[str, str]],
) -> list[dict[str, dict[str, str]]]:
    return [
        {"prediction": predictions[prediction_id], "verification": verification}
        for prediction_id, verification in sorted(verifications.items())
    ]


def validate_immutable_prediction_files(base_ref: str, repo_root: Path = ROOT) -> None:
    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-status",
            "--find-renames",
            base_ref,
            "--",
            "docs/predictions",
        ],
        cwd=repo_root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValidationError(
            f"could not compare prediction files with {base_ref}: "
            f"{result.stderr.strip()}"
        )
    violations: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        status = parts[0]
        paths = parts[1:]
        csv_paths = [path for path in paths if path.lower().endswith(".csv")]
        if not csv_paths:
            continue
        if status != "A":
            violations.append(line)
    if violations:
        raise ValidationError(
            "existing prediction CSV files are immutable; only additions are allowed: "
            + "; ".join(violations)
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate immutable prediction and verification CSV records."
    )
    parser.add_argument(
        "--base-ref",
        help="Git revision used to reject edits/deletes of existing prediction CSVs",
    )
    parser.add_argument(
        "--skip-snapshot-check",
        action="store_true",
        help="Skip source_snapshot existence and hash checks",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.base_ref:
            validate_immutable_prediction_files(args.base_ref)
        predictions = load_prediction_records(validate_snapshots=not args.skip_snapshot_check)
        verifications = load_verification_records(predictions)
    except ValidationError as exc:
        print(f"prediction validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(
        f"prediction validation succeeded: {len(predictions)} predictions, "
        f"{len(verifications)} verifications"
    )


if __name__ == "__main__":
    main()
