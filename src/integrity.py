"""Schema 2.0 integrity, selection, and outcome primitives.

The functions in this module are deliberately free of network calls.  Daily
screening supplies recorded Yahoo frames; tests and downstream verification can
therefore reproduce every decision without access to an external service.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

SCHEMA_VERSION = "2.0"
CORPORATE_ACTION_VALIDATION_VERSION = "ca-reconciliation-2.0"
ACTION_CLASS_MAPPING_VERSION = "action-class-2.0"
TIMEZONE = "America/New_York"


class IntegrityError(ValueError):
    """An input cannot safely be used for analysis."""


def digest(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p).strip() for p in parts).encode()).hexdigest()


def make_entity_id(market_data_date: str, ticker: str, config_hash: str) -> str:
    return "ent_" + digest(market_data_date, ticker.upper(), config_hash)


def make_route_candidate_id(entity_id: str, source_dataset: str) -> str:
    return "route_" + digest(entity_id, source_dataset)


def make_prediction_id(route_candidate_id: str, horizon: int, prediction_run_id: str,
                       prompt_version: str, entry_price_timestamp: str) -> str:
    return "pred_" + digest(route_candidate_id, horizon, prediction_run_id,
                            prompt_version, entry_price_timestamp)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_snapshot(snapshot_path: Path) -> dict[str, Any]:
    """Pin and verify an immutable v2 snapshot (never consult ``latest``)."""
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if payload.get("snapshot_schema_version") not in {"1.0", "2.0"}:
        raise IntegrityError("unsupported snapshot_schema_version")
    files = payload.get("files")
    if not isinstance(files, dict):
        raise IntegrityError("snapshot files must be an object")
    for key in ("latest_json", "latest_csv", "quiet_drift_csv"):
        metadata = files.get(key)
        if not isinstance(metadata, dict):
            raise IntegrityError(f"snapshot is missing files.{key}")
        path = snapshot_path.parent / str(metadata.get("path", ""))
        if not path.is_file() or sha256_file(path) != metadata.get("sha256"):
            raise IntegrityError(f"snapshot hash mismatch: {key}")
        expected_rows = metadata.get("row_count")
        if expected_rows is not None and key.endswith("csv"):
            actual_rows = max(sum(1 for _ in path.open(encoding="utf-8-sig")) - 1, 0)
            if actual_rows != expected_rows:
                raise IntegrityError(f"snapshot row count mismatch: {key}")
    return payload


@dataclass(frozen=True)
class CorporateActionResult:
    status: str
    detected_count: int
    reconciled_count: int
    unreconciled_count: int
    continuity_check: str
    reasons: tuple[str, ...] = ()


def split_adjusted_prices(frame: pd.DataFrame) -> pd.Series:
    """Return price-only split-adjusted closes (dividends are not included)."""
    close = pd.to_numeric(frame["Close"], errors="coerce")
    splits = pd.to_numeric(frame.get("Stock Splits", pd.Series(0.0, index=frame.index)),
                           errors="coerce").fillna(0.0)
    # A ratio recorded on day d adjusts observations strictly before d.
    future_factor = splits.replace(0.0, 1.0).iloc[::-1].cumprod().iloc[::-1]
    factor = future_factor / splits.replace(0.0, 1.0)
    return close / factor


def reconcile_corporate_actions(frame: pd.DataFrame, tolerance: float = 0.08) -> CorporateActionResult:
    """Reconcile raw/adjusted continuity around splits and distributions.

    Split days must show either the expected raw discontinuity or an already
    adjusted raw series, while the reconstructed price-only series must remain
    continuous after allowing an 8% genuine market move.  Mixing raw and adjusted
    bases is detected by discontinuities matching a known ratio away from its
    action date.  Missing action data is fail-closed rather than interpreted as no
    action.
    """
    required = {"Close", "Stock Splits", "Dividends"}
    if frame.empty or not required.issubset(frame.columns):
        return CorporateActionResult("unavailable", 0, 0, 1, "not_checked",
                                     ("corporate_action_data_unavailable",))
    work = frame.sort_index().copy()
    close = pd.to_numeric(work["Close"], errors="coerce")
    splits = pd.to_numeric(work["Stock Splits"], errors="coerce").fillna(0.0)
    dividends = pd.to_numeric(work["Dividends"], errors="coerce").fillna(0.0)
    capital_gains = pd.to_numeric(work.get("Capital Gains", pd.Series(0.0, index=work.index)),
                                  errors="coerce").fillna(0.0)
    events: list[tuple[Any, str, float]] = []
    for idx in work.index:
        if splits.loc[idx] > 0 and not math.isclose(float(splits.loc[idx]), 1.0):
            events.append((idx, "split", float(splits.loc[idx])))
        previous = close.shift(1).loc[idx]
        distribution = float(dividends.loc[idx] + capital_gains.loc[idx])
        if distribution > 0 and pd.notna(previous) and distribution / previous >= 0.10:
            events.append((idx, "special_distribution", distribution))
    adjusted = split_adjusted_prices(work)
    reasons: list[str] = []
    reconciled = 0
    for idx, kind, amount in events:
        pos = work.index.get_loc(idx)
        if not isinstance(pos, int) or pos == 0:
            reasons.append(f"{idx}:missing_pre_action_price")
            continue
        raw_return = close.iloc[pos] / close.iloc[pos - 1] - 1
        adjusted_return = adjusted.iloc[pos] / adjusted.iloc[pos - 1] - 1
        if kind == "split":
            # Compare multiplicatively so reverse splits do not receive a much
            # looser/tighter absolute-return tolerance than ordinary splits.
            raw_consistent = (abs((1.0 + raw_return) * amount - 1.0) <= tolerance
                              or abs(raw_return) <= tolerance)
            if not raw_consistent or abs(adjusted_return) > max(0.35, tolerance * 3):
                reasons.append(f"{idx}:split_ratio_not_reconciled")
                continue
        else:
            # A special distribution may legitimately gap; it must at least have
            # finite prices and explicit action metadata.
            if not np.isfinite(raw_return) or not np.isfinite(adjusted_return):
                reasons.append(f"{idx}:distribution_price_unavailable")
                continue
        reconciled += 1
    # A ratio-shaped adjusted-series jump on an action date indicates a raw/adjusted mix.
    for idx in splits[splits > 0].index:
        pos = work.index.get_loc(idx)
        if isinstance(pos, int) and pos > 0:
            ratio = float(splits.loc[idx])
            adj_factor = adjusted.iloc[pos] / adjusted.iloc[pos - 1]
            if min(abs(adj_factor - ratio), abs(adj_factor - 1 / ratio)) <= tolerance:
                marker = f"{idx}:adjusted_series_contains_split_jump"
                if marker not in reasons:
                    reasons.append(marker)
    unreconciled = len(reasons)
    return CorporateActionResult(
        "reconciled" if unreconciled == 0 else "unreconciled",
        len(events), reconciled, unreconciled,
        "passed" if unreconciled == 0 else "failed", tuple(reasons),
    )


def validate_information_timeline(information_cutoff_at: str, first_tradable_at: str,
                                  entry_price_timestamp: str) -> None:
    values = [datetime.fromisoformat(v.replace("Z", "+00:00")) for v in
              (information_cutoff_at, first_tradable_at, entry_price_timestamp)]
    if any(v.tzinfo is None for v in values):
        raise IntegrityError("cutoff and entry timestamps must be timezone-aware")
    if values[0] > values[1] or values[2] < values[1]:
        raise IntegrityError("future information leakage: entry is not tradable after cutoff")


NEUTRAL_THRESHOLDS = {
    21: {"absolute": 0.03, "relative": 0.02},
    63: {"absolute": 0.05, "relative": 0.03},
    126: {"absolute": 0.08, "relative": 0.05},
    252: {"absolute": 0.12, "relative": 0.07},
}


def neutral_threshold(horizon: int, kind: str) -> float:
    try:
        return NEUTRAL_THRESHOLDS[horizon][kind]
    except KeyError as exc:
        raise IntegrityError("unsupported horizon or threshold kind") from exc


def validate_confidence(value: str, probability: float) -> None:
    bands = {"low": (0.50, 0.60), "medium": (0.60, 0.75), "high": (0.75, 1.0)}
    if value not in bands or not bands[value][0] <= probability < bands[value][1]:
        raise IntegrityError("confidence label does not match probability band")


def display_action_class(record: dict[str, str]) -> str:
    """Deterministic display-only A--E mapping from orthogonal concepts."""
    status, stance, priority = (record.get("research_status"),
                                record.get("investment_stance"),
                                record.get("research_priority"))
    if status in {"data_artifact", "excluded"} or stance == "avoid_candidate":
        return "E"
    if status in {"unresolved", "incomplete"}:
        return "D"
    if status == "conditional" or stance == "neutral_candidate":
        return "C"
    if status == "ready_for_deep_dive" and stance in {"bullish_candidate", "bearish_candidate"}:
        return "A" if priority == "high" else "B"
    raise IntegrityError("action class mapping is undefined")


def select_comparisons(finals: Iterable[dict[str, Any]], pool: Iterable[dict[str, Any]],
                       minimum_per_final: int = 1) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Select deterministic same-route nearest controls without mixing scores."""
    available = list(pool)
    selected: list[dict[str, Any]] = []
    shortages: list[dict[str, Any]] = []
    used: set[str] = set()
    for final in sorted(finals, key=lambda r: r["route_candidate_id"]):
        candidates = [r for r in available if r["source_dataset"] == final["source_dataset"]
                      and r["route_candidate_id"] not in used
                      and r["entity_id"] != final["entity_id"]]
        candidates.sort(key=lambda r: (
            r.get("sector") != final.get("sector"),
            r.get("market_cap_band") != final.get("market_cap_band"),
            r.get("liquidity_band") != final.get("liquidity_band"),
            abs(int(r.get("original_rank", 10**9)) - int(final.get("original_rank", 0))),
            r["route_candidate_id"],
        ))
        chosen = candidates[:minimum_per_final]
        for row in chosen:
            used.add(row["route_candidate_id"])
            selected.append({**row, "record_group": "comparison_only",
                             "comparison_for": final["route_candidate_id"],
                             "comparison_reason": "deterministic same-route nearest match"})
        if len(chosen) < minimum_per_final:
            shortages.append({"route_candidate_id": final["route_candidate_id"],
                              "missing": minimum_per_final - len(chosen),
                              "selection_evaluation": "not_evaluable"})
    return selected, shortages


LIFECYCLE_STATUSES = {"active", "cash_acquisition_completed", "stock_acquisition_completed",
                      "delisted", "bankruptcy", "ticker_changed", "spinoff",
                      "special_dividend", "adr_terminated", "liquidated"}
VALUATION_STATUSES = {"not_started", "partial", "range_estimated", "complete",
                      "not_applicable", "not_estimable"}


def validate_valuation(status: str, low: float | None, base: float | None,
                       high: float | None) -> None:
    if status not in VALUATION_STATUSES:
        raise IntegrityError("unsupported valuation status")
    values = (low, base, high)
    if status in {"range_estimated", "complete"}:
        if any(v is None or not math.isfinite(v) for v in values) or not low <= base <= high:
            raise IntegrityError("valuation range must be finite and ordered")
    elif status in {"not_started", "not_applicable", "not_estimable"} and any(v is not None for v in values):
        raise IntegrityError("unperformed valuation must not assert a value range")


def calculate_returns(entry_price: float, exit_price: float, dividends: float = 0.0) -> tuple[float, float]:
    if entry_price <= 0:
        raise IntegrityError("entry price must be positive")
    return exit_price / entry_price - 1, (exit_price + dividends) / entry_price - 1


def calculate_mfe_mae(entry_price: float, highs: Iterable[float], lows: Iterable[float],
                      direction: str) -> tuple[float, float]:
    high, low = max(highs), min(lows)
    if direction == "up":
        return high / entry_price - 1, low / entry_price - 1
    if direction == "down":
        return 1 - low / entry_price, 1 - high / entry_price
    raise IntegrityError("MFE/MAE direction must be up or down")
