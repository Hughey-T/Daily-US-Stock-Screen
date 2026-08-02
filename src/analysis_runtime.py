"""Production persistence boundary for contract 3.0 analysis artifacts.

The GitHub Issue workflow is the only writer. This module derives candidates
from the pinned machine generation, calls :mod:`src.ai_analysis` validators,
and writes content-addressed, append-only artifacts for public read-back.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from src.ai_analysis import (
    blind_projection,
    candidate_set_id,
    canonical_hash,
    integrate,
    integration_basis,
    reconcile,
    reconciliation_status,
    strict_json_loads,
    validate_assessments,
    validate_ledger,
)
from src.integrity import verify_snapshot

RECONCILIATION_REVISION = "phase5-runtime-v1"


class RuntimeRequestError(ValueError):
    """A trusted workflow request is invalid or conflicts with immutable state."""


def _write_new(path: Path, value: Any) -> str:
    data = (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
    if path.exists():
        if path.read_bytes() == data:
            return hashlib.sha256(data).hexdigest()
        raise RuntimeRequestError("IMMUTABLE_ARTIFACT_CONFLICT")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def _candidate_index(items: Any, section: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise RuntimeRequestError(f"RECONCILIATION_SECTION_NOT_ARRAY:{section}")
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("candidate_id"), str):
            raise RuntimeRequestError(f"RECONCILIATION_CANDIDATE_INVALID:{section}")
        candidate_id = item["candidate_id"]
        if candidate_id in indexed:
            raise RuntimeRequestError(f"RECONCILIATION_CANDIDATE_DUPLICATE:{section}")
        indexed[candidate_id] = item
    return indexed


def upgrade_reconciliation_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    """Derive runtime-owned Phase 5 fields for both current and legacy bundles."""
    updated = deepcopy(bundle)
    projection = _candidate_index(updated.get("reconciliation_projection"), "reconciliation_projection")
    decisions = _candidate_index(updated.get("integrated_decisions"), "integrated_decisions")
    handoffs = _candidate_index(updated.get("reconciliation_handoffs"), "reconciliation_handoffs")
    ledger = _candidate_index(updated.get("decision_ledger"), "decision_ledger")
    candidate_ids = list(projection)
    expected = set(candidate_ids)
    if not candidate_ids or any(set(indexed) != expected for indexed in (decisions, handoffs, ledger)):
        raise RuntimeRequestError("RECONCILIATION_CANDIDATE_COVERAGE_MISMATCH")

    for candidate_id in candidate_ids:
        joined = projection[candidate_id]
        signals = joined.get("mechanical_signals")
        assessment = joined.get("ai_judgment")
        if not isinstance(signals, dict) or not isinstance(assessment, dict):
            raise RuntimeRequestError("RECONCILIATION_INPUTS_INCOMPLETE")
        candidate = {"mechanical_signals": signals}
        decision = integrate(candidate, assessment)
        basis = integration_basis(candidate, assessment, decision)
        comparison_status = reconciliation_status(candidate, assessment, decision)

        joined["integrated_decision"] = decision
        joined["comparison_status"] = comparison_status
        joined["integration_basis"] = basis

        decision_item = decisions[candidate_id]
        decision_item["decision"] = decision
        decision_item["comparison_status"] = comparison_status
        decision_item["integration_basis"] = basis

        handoff = handoffs[candidate_id]
        handoff["integrated_decision"] = decision
        handoff["comparison_status"] = comparison_status
        handoff["integration_basis"] = basis
        handoff["disagreement"] = comparison_status

        decision_layers = ledger[candidate_id].get("decisions")
        if not isinstance(decision_layers, dict):
            raise RuntimeRequestError("RECONCILIATION_LEDGER_INVALID")
        decision_layers["integrated"] = decision

    updated["reconciliation_contract_revision"] = RECONCILIATION_REVISION
    validate_ledger(updated["decision_ledger"])
    return updated


def _write_bundle_result(root: Path, bundle: dict[str, Any], status: str) -> dict[str, Any]:
    generation_id = bundle.get("generation_id")
    if not isinstance(generation_id, str):
        raise RuntimeRequestError("BUNDLE_GENERATION_INVALID")
    base = root / "docs/analysis/v3" / generation_id
    bundle_id = "analysis_" + canonical_hash(bundle)
    bundle_path = base / "sessions" / f"{bundle_id}.json"
    bundle_sha = _write_new(bundle_path, bundle)
    index = {
        "analysis_contract_version": "3.0",
        "generation_id": generation_id,
        "active_bundle_path": bundle_path.relative_to(root).as_posix(),
        "active_bundle_sha256": bundle_sha,
    }
    _write_new(base / "index" / f"{bundle_id}.json", index)
    return {
        "status": status,
        "bundle_id": bundle_id,
        "path": bundle_path.relative_to(root).as_posix(),
        "sha256": bundle_sha,
        "bundle": bundle,
    }


def candidates_from_snapshot(snapshot_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    snapshot = verify_snapshot(snapshot_path)
    phase3_path = snapshot_path.parent / snapshot["files"]["phase3_artifact"]["path"]
    phase3 = strict_json_loads(phase3_path.read_bytes())
    candidates = []
    for row in phase3["final_research_set"]:
        route = row["source_dataset"]
        facts = {key: row.get(key) for key in ("ticker", "company_name", "sector", "industry", "market_data_date")}
        metrics = {
            key: value
            for key, value in row.items()
            if key.startswith(
                ("return_", "spy_", "sector_", "volume_", "volatility_", "max_", "directional_", "relative_")
            )
        }
        candidates.append(
            {
                "candidate_id": row["route_candidate_id"],
                "generation_id": snapshot["generation_id"],
                "security_identity": row["entity_id"],
                "ticker": row["ticker"],
                "route": route,
                "facts": facts,
                "measured_metrics": metrics,
                "evidence_refs": row.get("evidence_refs", []),
                "evidence_cutoff": snapshot["market_data_cutoff_at"],
                "mechanical_signals": {
                    "mechanical_rank": row["original_rank"],
                    "selection_bucket": row.get("selection_bucket", "not_available"),
                    "mechanical_priority": row.get("treatment", "not_available"),
                    "threshold_passes": row.get("threshold_passes", []),
                    "threshold_failures": row.get("threshold_failures", []),
                    "quality_gate": "passed" if row.get("data_quality_status") == "passed" else "failed",
                    "hard_exclusions": [] if row.get("data_quality_status") == "passed" else ["DATA_ANOMALY"],
                },
            }
        )
    return snapshot, candidates


def publish_blind_projection(root: Path, snapshot_path: Path) -> dict[str, Any]:
    snapshot, candidates = candidates_from_snapshot(snapshot_path)
    cset = candidate_set_id(candidates)
    artifact = {
        "analysis_contract_version": "3.0",
        "generation_id": snapshot["generation_id"],
        "candidate_set_id": cset,
        "evidence_cutoff": snapshot["market_data_cutoff_at"],
        "candidates": [blind_projection(candidate) for candidate in candidates],
    }
    path = root / "docs/analysis/v3" / snapshot["generation_id"] / "blind-projection.json"
    sha = _write_new(path, artifact)
    return {"path": path.relative_to(root).as_posix(), "sha256": sha, "artifact": artifact}


def persist_assessment(root: Path, snapshot_path: Path, request: dict[str, Any]) -> dict[str, Any]:
    required = {
        "operation",
        "request_id",
        "nonce",
        "repository",
        "source_snapshot_path",
        "submitted_at",
        "artifact",
        "evidence_registry",
    }
    if not required.issubset(request) or request["operation"] != "persist_ai_assessment_v3":
        raise RuntimeRequestError("REQUEST_CONTRACT_INVALID")
    request_payload_hash = canonical_hash([request["artifact"], request["evidence_registry"]])
    expected_request_id = "analysis_" + request_payload_hash
    if request["request_id"] != expected_request_id:
        raise RuntimeRequestError("REQUEST_ID_MISMATCH")
    ledger_path = root / "docs/analysis/v3/write-ledger.json"
    ledger_state = cast(
        dict[str, Any],
        (strict_json_loads(ledger_path.read_bytes()) if ledger_path.exists() else {"version": "3.0", "requests": []}),
    )
    by_id = next((entry for entry in ledger_state["requests"] if entry["request_id"] == request["request_id"]), None)
    if by_id:
        if by_id["artifact_hash"] != request_payload_hash:
            raise RuntimeRequestError("REQUEST_ID_PAYLOAD_CONFLICT")
        stored = root / by_id["path"]
        stored_bundle = strict_json_loads(stored.read_bytes())
        if not isinstance(stored_bundle, dict):
            raise RuntimeRequestError("STORED_BUNDLE_INVALID")
        upgraded = upgrade_reconciliation_bundle(stored_bundle)
        if upgraded != stored_bundle:
            return _write_bundle_result(root, upgraded, "idempotent_replay_migrated")
        return {
            "status": "idempotent_replay",
            "bundle_id": by_id["bundle_id"],
            "path": by_id["path"],
            "sha256": hashlib.sha256(stored.read_bytes()).hexdigest(),
            "bundle": stored_bundle,
        }
    if any(entry["nonce"] == request["nonce"] for entry in ledger_state["requests"]):
        raise RuntimeRequestError("NONCE_REUSED")
    snapshot, candidates = candidates_from_snapshot(snapshot_path)
    artifact = request["artifact"]
    cutoff = datetime.fromisoformat(artifact["evidence_cutoff"].replace("Z", "+00:00"))
    registry: dict[str, list[str]] = {candidate["candidate_id"]: [] for candidate in candidates}
    for evidence in request["evidence_registry"]:
        if evidence["candidate_id"] not in registry:
            raise RuntimeRequestError("UNKNOWN_EVIDENCE_OWNER")
        published = datetime.fromisoformat(evidence["published_at"].replace("Z", "+00:00"))
        if published > cutoff:
            raise RuntimeRequestError("FUTURE_EVIDENCE")
        registry[evidence["candidate_id"]].append(evidence["evidence_id"])
    for candidate in candidates:
        candidate["evidence_refs"] = registry[candidate["candidate_id"]]
    schema = strict_json_loads((root / "schemas/v3.0/ai-assessment.schema.json").read_bytes())
    ai_id = validate_assessments(artifact, candidates, schema)
    locked = {**artifact, "artifact_hash": ai_id, "locked_at": request["submitted_at"]}
    base = root / "docs/analysis/v3" / snapshot["generation_id"]
    assessment_path = base / "ai-judgments" / f"{ai_id}.json"
    assessment_sha = _write_new(assessment_path, locked)
    reconciled = reconcile(candidates, locked, ai_id)
    decisions = []
    blind_handoffs = []
    reconciliation_handoffs = []
    ledger = []
    for candidate, joined in zip(candidates, reconciled, strict=True):
        ai = joined["ai_judgment"]
        decision = integrate(candidate, ai)
        cid = candidate["candidate_id"]
        hid = "hand_" + canonical_hash([snapshot["generation_id"], cid])
        blind_handoffs.append(
            {
                "handoff_id": hid,
                "generation_id": snapshot["generation_id"],
                "candidate_id": cid,
                "ticker": candidate["ticker"],
                "security_identity": candidate["security_identity"],
                "facts": candidate["facts"],
                "source_refs": candidate["evidence_refs"],
                "data_quality": {"quality_gate": candidate["mechanical_signals"]["quality_gate"]},
                "investment_horizon": ai["expected_resolution_horizon"],
                "unresolved_factual_questions": ai["additional_research"],
            }
        )
        reconciliation_handoffs.append(
            {
                "handoff_id": hid + "_reconciliation",
                "blind_handoff_id": hid,
                "generation_id": snapshot["generation_id"],
                "candidate_id": cid,
                "mechanical_signals": candidate["mechanical_signals"],
                "ai_judgment": ai,
                "integrated_decision": decision,
                "upstream_hypotheses": [ai["primary_hypothesis"]],
                "disagreement": ai["mechanical_agreement"],
                "unresolved_issues": ai["uncertainties"],
                "status": "active",
            }
        )
        decisions.append({"candidate_id": cid, "decision": decision})
        ledger.append(
            {
                "generation_id": snapshot["generation_id"],
                "candidate_id": cid,
                "decisions": {
                    "mechanical": candidate["mechanical_signals"]["mechanical_priority"],
                    "ai_independent": ai["downstream_suitability"],
                    "integrated": decision,
                },
                "as_of": request["submitted_at"],
                "source_cutoff": artifact["evidence_cutoff"],
                "ai_artifact_hash": ai_id,
            }
        )
    validate_ledger(ledger)
    bundle = {
        "analysis_contract_version": "3.0",
        "generation_id": snapshot["generation_id"],
        "candidate_set_id": candidate_set_id(candidates),
        "ai_artifact_path": assessment_path.relative_to(root).as_posix(),
        "ai_artifact_sha256": assessment_sha,
        "reconciliation_projection": reconciled,
        "integrated_decisions": decisions,
        "exploratory_proposals": request.get("exploratory_proposals", []),
        "blind_handoffs": blind_handoffs,
        "reconciliation_handoffs": reconciliation_handoffs,
        "decision_ledger": ledger,
        "outcomes": [{"candidate_id": c["candidate_id"], "status": "not_matured"} for c in candidates],
    }
    previous_path_value = request.get("previous_bundle_path")
    if previous_path_value:
        previous_path = (root / previous_path_value).resolve()
        analysis_root = (root / "docs/analysis/v3").resolve()
        if analysis_root not in previous_path.parents or not previous_path.is_file():
            raise RuntimeRequestError("UNSAFE_PREVIOUS_BUNDLE")
        previous = strict_json_loads(previous_path.read_bytes())
        superseded = supersede_handoffs(previous, snapshot["generation_id"])
        superseded_path = base / "superseded-handoffs" / (canonical_hash(superseded) + ".json")
        _write_new(superseded_path, superseded)
        bundle["superseded_handoff_artifact_path"] = superseded_path.relative_to(root).as_posix()
        bundle["previous_generation_id"] = previous["generation_id"]
    bundle = upgrade_reconciliation_bundle(bundle)
    result = _write_bundle_result(root, bundle, "persisted")
    ledger_state["requests"].append(
        {
            "request_id": request["request_id"],
            "nonce": request["nonce"],
            "artifact_hash": request_payload_hash,
            "bundle_id": result["bundle_id"],
            "path": result["path"],
            "recorded_at": request["submitted_at"],
        }
    )
    # The ledger is an append-only index: existing entries are never modified.
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(ledger_state, sort_keys=True, indent=2) + "\n")
    return result


def verify_readback(root: Path, result: dict[str, Any]) -> dict[str, Any]:
    path = root / result["path"]
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != result["sha256"]:
        raise RuntimeRequestError("REMOTE_HASH_MISMATCH")
    return strict_json_loads(path.read_bytes())


def supersede_handoffs(old_bundle: dict[str, Any], new_generation_id: str) -> dict[str, Any]:
    updated = json.loads(json.dumps(old_bundle))
    for handoff in updated["reconciliation_handoffs"]:
        handoff["status"] = "superseded"
        handoff["superseded_by_generation_id"] = new_generation_id
    return updated
