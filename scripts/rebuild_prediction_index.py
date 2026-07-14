from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import exchange_calendars as xcals
import pandas as pd

try:
    from scripts.validate_prediction_record import (
        PREDICTIONS_DIR,
        ROOT,
        ValidationError,
        load_prediction_records,
    )
except ModuleNotFoundError:  # Direct execution adds scripts/, not the repository root.
    from validate_prediction_record import (
        PREDICTIONS_DIR,
        ROOT,
        ValidationError,
        load_prediction_records,
    )


INDEX_PATH = PREDICTIONS_DIR / "index.json"


def _single(rows: list[dict[str, str]], column: str, path: Path) -> str:
    values = {row[column] for row in rows}
    if len(values) != 1:
        raise ValidationError(f"{path}: {column} must have one value per prediction file")
    return next(iter(values))


def _verification_date(market_data_date: str, horizon: int) -> str:
    calendar = xcals.get_calendar("XNYS")
    session = pd.Timestamp(market_data_date)
    if not calendar.is_session(session):
        raise ValidationError(f"market_data_date is not an XNYS session: {market_data_date}")
    return pd.Timestamp(calendar.session_offset(session, horizon)).date().isoformat()


def build_index(
    predictions_dir: Path = PREDICTIONS_DIR,
    repo_root: Path = ROOT,
    validate_snapshots: bool = True,
) -> dict[str, Any]:
    records = load_prediction_records(
        predictions_dir=predictions_dir,
        repo_root=repo_root,
        validate_snapshots=validate_snapshots,
    )
    by_file: dict[Path, list[dict[str, str]]] = {}
    for record in records.values():
        by_file.setdefault(Path(record["_file"]), []).append(record)

    entries: list[dict[str, Any]] = []
    for path in sorted(by_file):
        rows = by_file[path]
        market_data_date = _single(rows, "market_data_date", path)
        run_date = _single(rows, "run_date", path)
        horizons = sorted({int(row["verification_horizon"]) for row in rows})
        try:
            prediction_file = path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError as exc:
            raise ValidationError(f"prediction file is outside repository: {path}") from exc
        entries.append(
            {
                "prediction_file": prediction_file,
                "market_data_date": market_data_date,
                "created_at": f"{run_date}T00:00:00Z",
                "prediction_count": len(rows),
                "verification_horizons": horizons,
                "earliest_verification_dates": {
                    str(horizon): _verification_date(market_data_date, horizon)
                    for horizon in horizons
                },
                "prompt_version": _single(rows, "prompt_version", path),
                "config_version": _single(rows, "config_version", path),
                "config_hash": _single(rows, "config_hash", path),
                "file_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    entries.sort(key=lambda item: (item["market_data_date"], item["prediction_file"]))
    return {"prediction_schema_version": "1.0", "predictions": entries}


def write_index(payload: dict[str, Any], index_path: Path = INDEX_PATH) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(rendered, encoding="utf-8", newline="\n")


def main() -> None:
    try:
        payload = build_index()
        write_index(payload)
    except ValidationError as exc:
        print(f"prediction index rebuild failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(f"rebuilt {INDEX_PATH.relative_to(ROOT)} with {len(payload['predictions'])} files")


if __name__ == "__main__":
    main()
