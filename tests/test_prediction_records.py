from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.rebuild_prediction_index import build_index, write_index
from scripts.validate_prediction_record import (
    PREDICTION_REQUIRED_COLUMNS,
    VERIFICATION_REQUIRED_COLUMNS,
    ValidationError,
    join_prediction_verification_records,
    load_prediction_records,
    load_verification_records,
    make_candidate_id,
    make_prediction_id,
    validate_immutable_prediction_files,
)
from src.screen import save_daily_snapshot


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class PredictionRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.predictions_dir = self.root / "docs" / "predictions"
        self.verifications_dir = self.root / "docs" / "verifications"
        self.predictions_dir.mkdir(parents=True)
        self.verifications_dir.mkdir(parents=True)
        self.snapshot = self._create_snapshot()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _create_snapshot(self) -> str:
        snapshot_dir = self.root / "docs" / "snapshots" / "2026-07-13"
        snapshot_dir.mkdir(parents=True)
        files: dict[str, dict[str, str]] = {}
        for key, filename in (
            ("latest_json", "latest.json"),
            ("latest_csv", "latest.csv"),
            ("quiet_drift_csv", "quiet_drift.csv"),
        ):
            content = f"fixture:{key}\n".encode()
            (snapshot_dir / filename).write_bytes(content)
            files[key] = {
                "path": filename,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        manifest = {
            "snapshot_schema_version": "1.0",
            "market_data_date": "2026-07-13",
            "files": files,
        }
        (snapshot_dir / "snapshot.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        return "docs/snapshots/2026-07-13/snapshot.json"

    def valid_prediction(self, **overrides: str) -> dict[str, str]:
        row = {
            "prediction_schema_version": "1.0",
            "prediction_id": "",
            "candidate_id": "",
            "run_date": "2026-07-14",
            "market_data_date": "2026-07-13",
            "verification_horizon": "21",
            "ticker": "ACME",
            "company_name": "Acme Corp",
            "record_group": "final_candidate",
            "source_dataset": "event_anomaly",
            "source_rank": "1",
            "selection_bucket": "threshold",
            "anchor_horizon": "21d",
            "drift_direction": "up",
            "trend_consistency": "same_direction",
            "entry_price": "101.25",
            "entry_price_definition": "split-adjusted close on market_data_date",
            "predicted_absolute_direction": "up",
            "predicted_sector_relative_direction": "outperform",
            "benchmark": "SPY",
            "sector_etf": "XLK",
            "confidence": "medium",
            "action_class": "watch",
            "thesis": "A testable prediction thesis.",
            "invalidation_condition": "Close below the stated support.",
            "source_snapshot": self.snapshot,
            "prompt_version": "prompt-v1",
            "config_version": "config-v1",
            "config_hash": "a" * 64,
        }
        row.update(overrides)
        if "candidate_id" not in overrides:
            row["candidate_id"] = make_candidate_id(
                row["market_data_date"],
                row["source_dataset"],
                row["ticker"],
                row["config_hash"],
            )
        if "prediction_id" not in overrides:
            row["prediction_id"] = make_prediction_id(
                row["candidate_id"], int(row["verification_horizon"])
            )
        return row

    def write_predictions(
        self,
        rows: list[dict[str, str]],
        fieldnames: list[str] | None = None,
        filename: str = "predictions_2026-07-13.csv",
    ) -> Path:
        path = self.predictions_dir / filename
        write_csv(path, rows, fieldnames or list(PREDICTION_REQUIRED_COLUMNS))
        return path

    def load(self) -> dict[str, dict[str, str]]:
        return load_prediction_records(self.predictions_dir, self.root)

    def test_valid_prediction_csv_passes(self) -> None:
        row = self.valid_prediction()
        self.write_predictions([row])
        records = self.load()
        self.assertEqual(list(records), [row["prediction_id"]])

    def test_missing_required_column_fails(self) -> None:
        row = self.valid_prediction()
        columns = [column for column in PREDICTION_REQUIRED_COLUMNS if column != "thesis"]
        self.write_predictions([{key: row[key] for key in columns}], columns)
        with self.assertRaisesRegex(ValidationError, "missing required columns"):
            self.load()

    def test_duplicate_prediction_id_fails(self) -> None:
        row = self.valid_prediction()
        self.write_predictions([row, row])
        with self.assertRaisesRegex(ValidationError, "duplicate prediction_id"):
            self.load()

    def test_invalid_entry_prices_fail(self) -> None:
        for value in ("", "0", "-1"):
            with self.subTest(entry_price=value):
                self.write_predictions([self.valid_prediction(entry_price=value)])
                with self.assertRaisesRegex(ValidationError, "entry_price"):
                    self.load()

    def test_undefined_horizon_fails(self) -> None:
        self.write_predictions([self.valid_prediction(verification_horizon="22")])
        with self.assertRaisesRegex(ValidationError, "verification_horizon"):
            self.load()

    def test_index_rebuild_is_deterministic(self) -> None:
        rows = [
            self.valid_prediction(verification_horizon="21"),
            self.valid_prediction(verification_horizon="63"),
        ]
        self.write_predictions(rows)
        first = build_index(self.predictions_dir, self.root)
        index_path = self.predictions_dir / "index.json"
        write_index(first, index_path)
        first_bytes = index_path.read_bytes()
        second = build_index(self.predictions_dir, self.root)
        write_index(second, index_path)
        self.assertEqual(first, second)
        self.assertEqual(first_bytes, index_path.read_bytes())
        self.assertEqual(first["predictions"][0]["verification_horizons"], [21, 63])
        self.assertEqual(
            first["predictions"][0]["created_at"], "2026-07-14T00:00:00Z"
        )

    def test_prediction_and_verification_join_by_prediction_id(self) -> None:
        prediction = self.valid_prediction()
        self.write_predictions([prediction])
        verification = {
            "prediction_id": prediction["prediction_id"],
            "verification_date": "2026-08-12",
            "verification_horizon": "21",
            "future_stock_return": "0.10",
            "future_spy_relative_return": "0.06",
            "future_sector_relative_return": "0.04",
            "max_favorable_excursion": "0.12",
            "max_adverse_excursion": "-0.03",
            "absolute_direction_hit": "true",
            "sector_relative_direction_hit": "true",
            "outcome": "hit",
            "verification_data_source": "split-adjusted close; dividends excluded",
        }
        write_csv(
            self.verifications_dir / "verification_2026-08-12.csv",
            [verification],
            list(VERIFICATION_REQUIRED_COLUMNS),
        )
        predictions = self.load()
        verifications = load_verification_records(predictions, self.verifications_dir)
        joined = join_prediction_verification_records(predictions, verifications)
        self.assertEqual(len(joined), 1)
        self.assertEqual(
            joined[0]["prediction"]["prediction_id"],
            joined[0]["verification"]["prediction_id"],
        )


class PredictionImmutabilityTests(unittest.TestCase):
    @staticmethod
    def initialize_repository(root: Path) -> Path:
        prediction_path = root / "docs" / "predictions" / "existing.csv"
        prediction_path.parent.mkdir(parents=True)
        prediction_path.write_text("prediction_id\noriginal\n", encoding="utf-8")
        commands = (
            ("git", "init", "-q"),
            ("git", "config", "user.name", "Test User"),
            ("git", "config", "user.email", "test@example.com"),
            ("git", "add", "."),
            ("git", "commit", "-qm", "base"),
            ("git", "tag", "base"),
        )
        for command in commands:
            subprocess.run(command, cwd=root, check=True, capture_output=True)
        return prediction_path

    def test_existing_prediction_file_modification_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction_path = self.initialize_repository(root)
            prediction_path.write_text("prediction_id\nmodified\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "immutable"):
                validate_immutable_prediction_files("base", root)

    def test_existing_prediction_file_deletion_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prediction_path = self.initialize_repository(root)
            prediction_path.unlink()
            with self.assertRaisesRegex(ValidationError, "immutable"):
                validate_immutable_prediction_files("base", root)


class DailySnapshotTests(unittest.TestCase):
    def test_existing_daily_snapshot_is_not_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docs = root / "docs"
            docs.mkdir()
            status_path = docs / "latest.json"
            latest_csv = docs / "latest.csv"
            quiet_csv = docs / "quiet_drift.csv"
            status_path.write_text(
                json.dumps({"generated_at": "2026-07-14T00:00:00+00:00"}),
                encoding="utf-8",
            )
            latest_csv.write_text("ticker\nACME\n", encoding="utf-8")
            quiet_csv.write_text("ticker\nQUIET\n", encoding="utf-8")
            replacements = {
                "SNAPSHOTS": docs / "snapshots",
                "STATUS_PATH": status_path,
                "LATEST_CSV": latest_csv,
                "QUIET_DRIFT_CSV": quiet_csv,
            }
            with patch.multiple("src.screen", **replacements):
                manifest_path = save_daily_snapshot("2026-07-13")
                original_snapshot = (manifest_path.parent / "latest.csv").read_bytes()
                latest_csv.write_text("ticker\nCHANGED\n", encoding="utf-8")
                repeated_path = save_daily_snapshot("2026-07-13")
            self.assertEqual(manifest_path, repeated_path)
            self.assertEqual(
                (manifest_path.parent / "latest.csv").read_bytes(), original_snapshot
            )


if __name__ == "__main__":
    unittest.main()
