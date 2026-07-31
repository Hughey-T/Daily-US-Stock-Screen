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
import exchange_calendars as xcals

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


def make_generation_id(market_data_date: str, config_hash: str,
                       authoritative_hashes: dict[str, str], code_version: str) -> str:
    """Identify every authoritative immutable input, including status JSON."""
    canonical=json.dumps(authoritative_hashes,sort_keys=True,separators=(",",":"))
    return "gen_"+digest(market_data_date,config_hash,canonical,code_version)


def validate_phase2_artifact(payload: dict[str, Any], generation_id: str) -> None:
    if payload.get("artifact_schema_version")!="2.0" or payload.get("generation_id")!=generation_id:
        raise IntegrityError("Phase 2 artifact schema/generation mismatch")
    rows=payload.get("processed_rows");audit=payload.get("audit")
    if not isinstance(rows,list) or not isinstance(audit,dict):raise IntegrityError("Phase 2 artifact structure invalid")
    treatments=("web_research","comparison_only","not_selected","data_artifact","unprocessable")
    for route in ("event_anomaly","quiet_drift"):
        route_rows=[r for r in rows if r.get("source_dataset")==route]
        item=audit.get(route,{})
        if item.get("total")!=len(route_rows) or item.get("unprocessed")!=0:raise IntegrityError(f"Phase 2 accounting invalid: {route}")
        actual={name:sum(r.get("treatment")==name for r in route_rows) for name in treatments}
        if any(item.get(name)!=count for name,count in actual.items()) or sum(actual.values())!=len(route_rows):raise IntegrityError(f"Phase 2 treatment identity invalid: {route}")
        if any(r.get("quantitative_processing_status") not in {"processed","unprocessable"} for r in route_rows):raise IntegrityError(f"Phase 2 unprocessed row: {route}")


def validate_phase3_artifact(payload: dict[str, Any], generation_id: str) -> None:
    if payload.get("artifact_schema_version")!="2.0" or payload.get("generation_id")!=generation_id:raise IntegrityError("Phase 3 artifact schema/generation mismatch")
    finals=payload.get("final_research_set",[]);comparisons=payload.get("comparison_records",[]);shortages=payload.get("shortage_records",[])
    entities=[r.get("entity_id") for r in finals]
    if len(finals)>15 or len(entities)!=len(set(entities)):raise IntegrityError("Phase 3 research entity constraint invalid")
    final_ids={r.get("route_candidate_id") for r in finals};matched={r.get("comparison_for") for r in comparisons}
    shortage_by={r.get("route_candidate_id"):r for r in shortages}
    for rid in final_ids:
        if rid not in matched:
            shortage=shortage_by.get(rid)
            if not shortage or shortage.get("selection_evaluation")!="not_evaluable" or int(shortage.get("missing",0))<1:raise IntegrityError("Phase 3 comparison/shortage mismatch")
    if any(r.get("comparison_for") not in final_ids for r in comparisons):raise IntegrityError("Phase 3 comparison_for is unknown")


def verify_snapshot(snapshot_path: Path) -> dict[str, Any]:
    """Pin and verify an immutable v2 snapshot (never consult ``latest``)."""
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if payload.get("snapshot_schema_version") not in {"1.0", "2.0"}:
        raise IntegrityError("unsupported snapshot_schema_version")
    files = payload.get("files")
    if not isinstance(files, dict):
        raise IntegrityError("snapshot files must be an object")
    required={"latest_json","latest_csv","quiet_drift_csv","phase2_artifact","phase3_artifact"}
    if not required.issubset(files):raise IntegrityError("snapshot is missing required files")
    for key,metadata in files.items():
        if not isinstance(metadata, dict):
            raise IntegrityError(f"snapshot is missing files.{key}")
        candidate = str(metadata.get("path", ""))
        if Path(candidate).is_absolute() or ".." in Path(candidate).parts:
            raise IntegrityError(f"snapshot path traversal: {key}")
        path = (snapshot_path.parent / candidate).resolve()
        if snapshot_path.parent.resolve() not in path.parents:
            raise IntegrityError(f"snapshot resource escapes generation: {key}")
        if not path.is_file() or sha256_file(path) != metadata.get("sha256"):
            raise IntegrityError(f"snapshot hash mismatch: {key}")
        expected_rows = metadata.get("row_count")
        if expected_rows is not None and key.endswith("csv"):
            with path.open(encoding="utf-8-sig") as handle:
                actual_rows = max(sum(1 for _ in handle) - 1, 0)
            if actual_rows != expected_rows:
                raise IntegrityError(f"snapshot row count mismatch: {key}")
        if key.endswith("artifact"):
            payload_value=json.loads(path.read_text(encoding="utf-8"))
            (validate_phase2_artifact if key=="phase2_artifact" else validate_phase3_artifact)(payload_value,payload.get("generation_id"))
            if metadata.get("record_count") != len(payload_value.get("processed_rows",payload_value.get("final_research_set",[]))):raise IntegrityError(f"snapshot artifact record count mismatch: {key}")
    return payload


def verify_manifest(manifest_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
    relative=Path(str(manifest.get("snapshot_path","")))
    if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "generations":
        raise IntegrityError("manifest snapshot path must stay under generations")
    snapshot_path=(manifest_path.parent/relative).resolve()
    if manifest_path.parent.resolve() not in snapshot_path.parents:
        raise IntegrityError("manifest snapshot path escapes publication root")
    if sha256_file(snapshot_path) != manifest.get("snapshot_sha256"):
        raise IntegrityError("manifest snapshot hash mismatch")
    snapshot=verify_snapshot(snapshot_path)
    if snapshot.get("snapshot_id") != manifest.get("snapshot_id") or snapshot.get("generation_id") != manifest.get("generation_id"):
        raise IntegrityError("manifest and snapshot identity mismatch")
    # At manifest-fetch time, compatibility latest files must describe the same
    # generation. A later session remains pinned and does not repeat this check.
    for key,name in (("latest_json","latest.json"),("latest_csv","latest.csv"),("quiet_drift_csv","quiet_drift.csv")):
        mutable=manifest_path.parent/name
        fixed=snapshot_path.parent/snapshot["files"][key]["path"]
        if mutable.exists() and mutable.read_bytes()!=fixed.read_bytes():
            raise IntegrityError(f"mutable latest diverges from manifest generation: {name}")
    return manifest,snapshot


@dataclass(frozen=True)
class CorporateActionResult:
    status: str
    detected_count: int
    reconciled_count: int
    unreconciled_count: int
    continuity_check: str
    reasons: tuple[str, ...] = ()
    action_details: tuple[dict[str, Any], ...] = ()


def _trading_prices_around(frame: pd.DataFrame, action_date: Any) -> tuple[Any, Any] | None:
    """Return the closest valid trading observations around an action date."""
    close = pd.to_numeric(frame["Close"], errors="coerce")
    before = close.loc[(frame.index < action_date) & close.notna()]
    after = close.loc[(frame.index >= action_date) & close.notna()]
    if before.empty or after.empty:
        return None
    return before.index[-1], after.index[0]


def _classify_split_basis(observed_factor: float, ratio: float,
                          continuity_limit: float = 0.35) -> tuple[str, str]:
    """Classify an event from observed continuity, never from column names."""
    adjusted_error = abs(observed_factor - 1.0)
    raw_error = abs(observed_factor * ratio - 1.0)
    if adjusted_error <= continuity_limit and adjusted_error <= raw_error:
        return "split_adjusted", "close_continuous_across_declared_split"
    if raw_error <= continuity_limit:
        return "raw_unadjusted", "observed_jump_matches_inverse_split_ratio"
    return "unknown", "observed_factor_matches_neither_supported_basis"


def split_adjusted_prices(frame: pd.DataFrame) -> pd.Series:
    """Normalize Close to a price-only split-adjusted basis.

    Yahoo's chart ``Close`` can already be split-adjusted even when yfinance is
    called with ``auto_adjust=False``. Only events whose observed price jump
    matches the inverse declared ratio are transformed.
    """
    normalized = pd.to_numeric(frame["Close"], errors="coerce").astype(float).copy()
    splits = pd.to_numeric(frame.get("Stock Splits", pd.Series(0.0, index=frame.index)),
                           errors="coerce").fillna(0.0)
    stock_dividends = pd.to_numeric(
        frame.get("Stock Dividends", pd.Series(0.0, index=frame.index)),
        errors="coerce").fillna(0.0)
    ratios = splits.where(splits != 0.0, 1.0 + stock_dividends)
    for action_date, ratio in ratios[ratios != 0.0].items():
        if math.isclose(float(ratio), 1.0):
            continue
        selected = _trading_prices_around(frame, action_date)
        if selected is None:
            continue
        before_date, after_date = selected
        observed = float(frame.loc[after_date, "Close"]) / float(frame.loc[before_date, "Close"])
        basis, _ = _classify_split_basis(observed, float(ratio))
        if basis == "raw_unadjusted":
            normalized.loc[normalized.index < after_date] /= float(ratio)
    return normalized


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
        return CorporateActionResult("unavailable", 0, 0, 0, "not_checked",
                                     ("corporate_action_data_unavailable",),())
    work = frame.sort_index().copy()
    close = pd.to_numeric(work["Close"], errors="coerce")
    splits = pd.to_numeric(work["Stock Splits"], errors="coerce").fillna(0.0)
    dividends = pd.to_numeric(work["Dividends"], errors="coerce").fillna(0.0)
    capital_gains = pd.to_numeric(work.get("Capital Gains", pd.Series(0.0, index=work.index)),
                                  errors="coerce").fillna(0.0)
    stock_dividends = pd.to_numeric(work.get("Stock Dividends", pd.Series(0.0,index=work.index)),errors="coerce").fillna(0.0)
    events: list[tuple[Any, str, float]] = []
    for idx in work.index:
        if splits.loc[idx] > 0 and not math.isclose(float(splits.loc[idx]), 1.0):
            events.append((idx, "split", float(splits.loc[idx])))
        if stock_dividends.loc[idx] > 0:
            events.append((idx,"stock_dividend",1.0+float(stock_dividends.loc[idx])))
        previous = close.shift(1).loc[idx]
        distribution = float(dividends.loc[idx] + capital_gains.loc[idx])
        if distribution > 0 and pd.notna(previous) and distribution / previous >= 0.10:
            events.append((idx, "special_distribution", distribution))
    normalized = split_adjusted_prices(work)
    reasons: list[str] = []; details=[]
    reconciled = 0
    for idx, kind, amount in events:
        selected = _trading_prices_around(work, idx)
        if selected is None:
            reasons.append(f"{idx}:missing_pre_action_price")
            details.append({"date":str(idx),"action_type":kind,"status":"unreconciled",
                            "source_price_basis":"unknown",
                            "reconciliation_method":"adjacent_trading_date_selection",
                            "classification_reason":"missing_price_around_action_date",
                            "selected_trading_dates":[]})
            continue
        before_date, after_date = selected
        close_before, close_after = float(close.loc[before_date]), float(close.loc[after_date])
        observed_factor = close_after / close_before
        normalized_factor = float(normalized.loc[after_date]) / float(normalized.loc[before_date])
        detail={"date":str(idx),"action_type":kind,"close_before":close_before,
                "close_after":close_after,"observed_factor":observed_factor,
                "raw_close_before":close_before,"raw_close_after":close_after,
                "reconstructed_close_before":float(normalized.loc[before_date]),
                "reconstructed_close_after":float(normalized.loc[after_date]),
                "selected_trading_dates":[str(before_date),str(after_date)],
                "auto_adjust_requested":bool(work.attrs.get("auto_adjust_requested",False)),
                "auto_adjust_observed":False if "Adj Close" in work.columns else "unknown",
                "adj_close_available":"Adj Close" in work.columns,
                "declared_split_ratio":amount if kind in {"split","stock_dividend"} else None,
                "distribution_amount":amount if kind=="special_distribution" else None}
        if kind in {"split","stock_dividend"}:
            basis, classification = _classify_split_basis(observed_factor, amount)
            detail.update({"source_price_basis":basis,"expected_factor":1.0/amount,
                           "expected_raw_factor":1.0/amount,
                           "reconciliation_method":"observed_factor_basis_classification",
                           "classification_reason":classification})
            if basis == "unknown" or abs(normalized_factor - 1.0) > max(0.35, tolerance * 3):
                reasons.append(f"{idx}:split_ratio_not_reconciled")
                detail["status"]="unreconciled"; details.append(detail)
                continue
        else:
            previous=close_before; current=close_after
            expected_factor=max(previous-amount,0.0)/previous
            actual_factor=current/previous
            # Distribution-adjusted continuity removes the known cash amount.
            continuity=(current+amount)/previous-1.0
            detail.update({"source_price_basis":"unknown",
                           "expected_factor":expected_factor,"reconciliation_method":"cash_distribution_continuity",
                           "expected_raw_factor":expected_factor,
                           "classification_reason":"cash_amount_added_back_to_close",
                           "actual_factor":actual_factor,"distribution_adjusted_return":continuity})
            if not np.isfinite(observed_factor) or not np.isfinite(continuity) or abs(continuity)>max(.20,tolerance*2):
                reasons.append(f"{idx}:distribution_not_reconciled")
                detail["status"]="unreconciled"; details.append(detail)
                continue
        reconciled += 1
        detail["status"]="reconciled"; details.append(detail)
    unreconciled = sum(detail.get("status")=="unreconciled" for detail in details)
    return CorporateActionResult(
        "reconciled" if unreconciled == 0 else "unreconciled",
        len(events), reconciled, unreconciled,
        "passed" if unreconciled == 0 else "failed", tuple(reasons),tuple(details),
    )


def market_session_timestamps(market_date: str) -> tuple[str,str]:
    """Actual XNYS close and next-session open, including DST/early closes."""
    calendar=xcals.get_calendar("XNYS"); session=pd.Timestamp(market_date)
    if not calendar.is_session(session): raise IntegrityError("market_data_date is not an XNYS session")
    schedule=calendar.schedule.loc[market_date:market_date]
    close=pd.Timestamp(schedule.iloc[0]["close"])
    next_session=calendar.next_session(session)
    next_label=next_session.date().isoformat()
    next_open=pd.Timestamp(calendar.schedule.loc[next_label:next_label].iloc[0]["open"])
    return close.isoformat(),next_open.isoformat()


def resolve_next_session_open(frame: pd.DataFrame, first_tradable_at: str,
                              observed_at: str) -> dict[str, Any]:
    first=datetime.fromisoformat(first_tradable_at.replace("Z","+00:00"));now=datetime.fromisoformat(observed_at.replace("Z","+00:00"))
    if now < first:
        return {"status":"pending","first_tradable_at":first_tradable_at,"entry_price_timestamp":None,"entry_price":None,"entry_price_type":"next_session_open"}
    if frame.empty or "Open" not in frame.columns:
        return {"status":"unavailable","first_tradable_at":first_tradable_at,"entry_price_timestamp":None,"entry_price":None,"entry_price_type":"next_session_open"}
    index=pd.to_datetime(frame.index,utc=True); matches=frame.loc[index.date==first.date()]
    if matches.empty:
        return {"status":"unavailable","first_tradable_at":first_tradable_at,"entry_price_timestamp":None,"entry_price":None,"entry_price_type":"next_session_open"}
    price=float(pd.to_numeric(matches.iloc[0]["Open"],errors="coerce"))
    if not math.isfinite(price) or price<=0:raise IntegrityError("next-session open is invalid")
    return {"status":"resolved","first_tradable_at":first_tradable_at,"entry_price_timestamp":first_tradable_at,"entry_price":price,"entry_price_type":"next_session_open"}


def corporate_action_failure_mode(results: dict[str, CorporateActionResult],
                                  unavailable_ratio: float, unreconciled_ratio: float,
                                  unreconciled_limit: int) -> str:
    total=max(len(results),1)
    unavailable=sum(r.status=="unavailable" for r in results.values())
    # Provider-unavailable data and genuinely unreconciled actions have separate
    # budgets.  Counting unavailable tickers in all three limits made a missing
    # yfinance action column look like a reconciliation defect.
    bad=sum(r.status=="unreconciled" for r in results.values())
    if unavailable/total>unavailable_ratio or bad/total>unreconciled_ratio or bad>unreconciled_limit:
        return "failed"
    return "degraded" if unavailable or bad else "success"


def validate_corporate_action_publication_status(status: dict[str, Any]) -> None:
    """Validate the success/degraded/failed publication state contract."""
    top = status.get("status")
    reconciliation = status.get("corporate_action_reconciliation")
    if top not in {"success", "degraded"}:
        raise IntegrityError(f"Invalid status: expected 'success' or 'degraded', got {top!r}")
    if not isinstance(reconciliation, dict):
        raise IntegrityError("corporate-action reconciliation metadata is missing")
    ca_status = reconciliation.get("status")
    unavailable = int(reconciliation.get("unavailable_ticker_count", 0))
    unreconciled = int(reconciliation.get("unreconciled_ticker_count", 0))
    if top == "success" and (ca_status != "reconciled" or unavailable or unreconciled):
        raise IntegrityError("success requires complete corporate-action reconciliation")
    if top == "degraded" and (ca_status != "degraded" or not (unavailable or unreconciled)):
        raise IntegrityError("degraded requires unavailable or unreconciled tickers")
    thresholds = reconciliation.get("configured_thresholds", {})
    if top == "degraded":
        required = {"max_unavailable_ratio", "max_unreconciled_ratio", "max_unreconciled_tickers"}
        if not required.issubset(thresholds):
            raise IntegrityError("degraded reconciliation thresholds are missing")
        if (float(reconciliation.get("unavailable_ratio", 0)) > float(thresholds["max_unavailable_ratio"])
                or float(reconciliation.get("unreconciled_ratio", 0)) > float(thresholds["max_unreconciled_ratio"])
                or unreconciled > int(thresholds["max_unreconciled_tickers"])):
            raise IntegrityError("degraded reconciliation exceeds configured threshold")


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
    for final in sorted(finals, key=lambda r: r["route_candidate_id"]):
        candidates = [r for r in available if r["source_dataset"] == final["source_dataset"]
                      and r["entity_id"] != final["entity_id"]]
        def strength(row:dict[str,Any])->float:
            return abs(float(row.get("tail_distance") or row.get("signal_score") or 0))
        candidates.sort(key=lambda r: (
            r.get("industry") != final.get("industry"),
            r.get("sector") != final.get("sector"),
            r.get("market_cap_band") != final.get("market_cap_band"),
            r.get("liquidity_band") != final.get("liquidity_band"),
            r.get("anchor_horizon") != final.get("anchor_horizon"),
            r.get("selection_bucket") != final.get("selection_bucket"),
            abs(strength(r)-strength(final)),
            abs(int(r.get("original_rank", 10**9)) - int(final.get("original_rank", 0))),
            r["route_candidate_id"],
        ))
        chosen = candidates[:minimum_per_final]
        for row in chosen:
            match={"same_route":True,"industry_match":row.get("industry")==final.get("industry"),"sector_match":row.get("sector")==final.get("sector"),"market_cap_band_match":row.get("market_cap_band")==final.get("market_cap_band"),"liquidity_band_match":row.get("liquidity_band")==final.get("liquidity_band"),"anchor_horizon_match":row.get("anchor_horizon")==final.get("anchor_horizon"),"selection_bucket_match":row.get("selection_bucket")==final.get("selection_bucket"),"anomaly_strength_distance":abs(strength(row)-strength(final)),"rank_distance":abs(int(row.get("original_rank",10**9))-int(final.get("original_rank",0)))}
            selected.append({**row, "record_group": "comparison_only",
                             "treatment": "comparison_only",
                             "comparison_for": final["route_candidate_id"],
                             "comparison_reason": "deterministic route/industry/sector/size/liquidity/anchor/bucket/strength match","comparison_match":match})
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
