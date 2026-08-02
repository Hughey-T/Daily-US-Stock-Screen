"""Fail-closed contracts for independent AI analysis (contract 3.0).

This module never fetches prices or invokes an LLM. It validates and advances
artifacts supplied by a Custom GPT while preserving producer-owned objects.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

CONTRACT_VERSION = "3.0"
INITIAL_PHASES = 8
UPDATE_PHASES = 3
RANK_KEYS = {"mechanical_rank", "mechanical_priority", "selection_bucket", "integrated_decision"}


class AnalysisIntegrityError(ValueError):
    """An analysis artifact cannot safely be accepted."""


def strict_json_loads(raw: bytes | str) -> Any:
    """Decode UTF-8 JSON, rejecting duplicate keys and non-JSON numbers."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="strict")

    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise AnalysisIntegrityError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(AnalysisIntegrityError(f"invalid JSON number: {value}")),
        )
    except UnicodeDecodeError as exc:
        raise AnalysisIntegrityError("invalid UTF-8") from exc


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def candidate_set_id(candidates: list[dict[str, Any]]) -> str:
    identities = sorted((c["candidate_id"], c["security_identity"], c["ticker"], c["route"]) for c in candidates)
    if len(identities) != len({item[0] for item in identities}):
        raise AnalysisIntegrityError("duplicate candidate")
    return "cset_" + canonical_hash(identities)


def blind_projection(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return facts and measured metrics without ranking or conclusions."""
    projection = {
        "candidate_id": candidate["candidate_id"],
        "generation_id": candidate["generation_id"],
        "route": candidate["route"],
        "facts": deepcopy(candidate["facts"]),
        "measured_metrics": deepcopy(candidate["measured_metrics"]),
        "evidence_refs": deepcopy(candidate["evidence_refs"]),
        "evidence_cutoff": candidate["evidence_cutoff"],
    }
    if any(key in json.dumps(projection) for key in RANK_KEYS):
        raise AnalysisIntegrityError("blind projection leaks mechanical ranking")
    return projection


def validate_assessments(artifact: dict[str, Any], candidates: list[dict[str, Any]], schema: dict[str, Any]) -> str:
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(artifact), key=lambda e: list(e.path)
    )
    if errors:
        raise AnalysisIntegrityError(errors[0].message)
    expected = {c["candidate_id"]: c for c in candidates}
    actual = artifact["assessments"]
    if len(actual) != len({a["candidate_id"] for a in actual}):
        raise AnalysisIntegrityError("duplicate AI candidate")
    if {a["candidate_id"] for a in actual} != set(expected):
        raise AnalysisIntegrityError("AI candidate coverage mismatch")
    cutoff = datetime.fromisoformat(artifact["evidence_cutoff"].replace("Z", "+00:00"))
    known_evidence = {ref for c in candidates for ref in c["evidence_refs"]}
    for assessment in actual:
        candidate = expected[assessment["candidate_id"]]
        if assessment["generation_id"] != candidate["generation_id"]:
            raise AnalysisIntegrityError("mixed generation")
        if assessment["mechanical_agreement"] != "INSUFFICIENT_EVIDENCE":
            raise AnalysisIntegrityError("blind assessment cannot claim mechanical agreement")
        refs = assessment["evidence_refs"]
        if len(refs) != len(set(refs)) or not set(refs).issubset(known_evidence):
            raise AnalysisIntegrityError("unknown or duplicate evidence")
        if assessment["ai_assessment_status"] == "assessed" and not refs:
            raise AnalysisIntegrityError("assessed candidate requires evidence")
        as_of = datetime.fromisoformat(assessment["analysis_as_of"].replace("Z", "+00:00"))
        if as_of < cutoff:
            raise AnalysisIntegrityError("analysis precedes evidence cutoff")
    if artifact["candidate_set_id"] != candidate_set_id(candidates):
        raise AnalysisIntegrityError("candidate-set identity mismatch")
    return "ai_" + canonical_hash(artifact)


def advance_session(state: dict[str, Any], command: str, generation_id: str) -> dict[str, Any]:
    """Advance exactly one phase. Only the exact Japanese controls are accepted."""
    result = deepcopy(state)
    if generation_id != result["generation_id"]:
        raise AnalysisIntegrityError("mixed generation")
    mode, phase = result["mode"], result["phase"]
    if command == "次":
        limit = INITIAL_PHASES if mode == "initial" else UPDATE_PHASES
        if phase >= limit:
            raise AnalysisIntegrityError("final phase already reached")
        result["phase"] = phase + 1
    elif command == "更新":
        if mode != "initial" or phase != INITIAL_PHASES:
            raise AnalysisIntegrityError("update requires completed initial analysis")
        result.update(mode="update", phase=1)
    else:
        raise AnalysisIntegrityError("command must be exactly 次 or 更新")
    return result


def reconcile(candidates: list[dict[str, Any]], artifact: dict[str, Any], ai_hash: str) -> list[dict[str, Any]]:
    """Expose producer signals only after the independent artifact is frozen."""
    if artifact.get("artifact_hash") != ai_hash or not artifact.get("locked_at"):
        raise AnalysisIntegrityError("AI assessment must be locked before reconciliation")
    indexed = {a["candidate_id"]: a for a in artifact["assessments"]}
    return [
        {
            "candidate_id": c["candidate_id"],
            "mechanical_signals": deepcopy(c["mechanical_signals"]),
            "ai_judgment": deepcopy(indexed[c["candidate_id"]]),
        }
        for c in candidates
    ]


def _likelihood_value(assessment: dict[str, Any], field: str) -> float | None:
    likelihood = assessment.get(field, {})
    value = likelihood.get("value")
    if likelihood.get("status") != "estimated" or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def integrate(candidate: dict[str, Any], assessment: dict[str, Any]) -> str:
    """Apply non-overridable quality gates and conservative research policy."""
    signals = candidate["mechanical_signals"]
    if signals.get("hard_exclusions") or signals.get("quality_gate") != "passed":
        return "REJECT_DATA_ANOMALY"
    if assessment["ai_assessment_status"] in {
        "not_assessed",
        "insufficient_evidence",
        "partially_assessed",
        "not_applicable",
    }:
        return "INSUFFICIENT_EVIDENCE"

    data_anomaly = _likelihood_value(assessment, "data_anomaly_likelihood")
    event_explained = _likelihood_value(assessment, "event_explained_likelihood")
    residual_mispricing = _likelihood_value(assessment, "residual_mispricing_likelihood")
    if data_anomaly is None or event_explained is None or residual_mispricing is None:
        return "INSUFFICIENT_EVIDENCE"
    if data_anomaly >= 0.7:
        return "REJECT_DATA_ANOMALY"
    if event_explained >= 0.8 and residual_mispricing < 0.4:
        return "REJECT_EXPLAINED_MOVE"
    if residual_mispricing >= 0.7:
        return "ADVANCE_TO_INDIVIDUAL_ANALYSIS"
    return "MONITOR"


def integration_basis(
    candidate: dict[str, Any],
    assessment: dict[str, Any],
    decision: str | None = None,
) -> dict[str, Any]:
    """Return the runtime-owned reason and inputs for an integrated decision."""
    signals = candidate["mechanical_signals"]
    hard_exclusions = deepcopy(signals.get("hard_exclusions", []))
    inputs = {
        "quality_gate": signals.get("quality_gate"),
        "hard_exclusions": hard_exclusions,
        "ai_assessment_status": assessment["ai_assessment_status"],
        "data_anomaly_likelihood": _likelihood_value(assessment, "data_anomaly_likelihood"),
        "event_explained_likelihood": _likelihood_value(assessment, "event_explained_likelihood"),
        "residual_mispricing_likelihood": _likelihood_value(assessment, "residual_mispricing_likelihood"),
    }
    resolved = decision or integrate(candidate, assessment)

    if hard_exclusions:
        reason_code = "MECHANICAL_HARD_EXCLUSION"
    elif signals.get("quality_gate") != "passed":
        reason_code = "MECHANICAL_QUALITY_GATE_FAILED"
    elif assessment["ai_assessment_status"] in {
        "not_assessed",
        "insufficient_evidence",
        "partially_assessed",
        "not_applicable",
    }:
        reason_code = "AI_ASSESSMENT_INCOMPLETE"
    elif any(
        inputs[field] is None
        for field in (
            "data_anomaly_likelihood",
            "event_explained_likelihood",
            "residual_mispricing_likelihood",
        )
    ):
        reason_code = "LIKELIHOODS_NOT_EVALUABLE"
    elif resolved == "REJECT_DATA_ANOMALY":
        reason_code = "AI_DATA_ANOMALY_HIGH"
    elif resolved == "REJECT_EXPLAINED_MOVE":
        reason_code = "MOVE_LARGELY_EXPLAINED"
    elif resolved == "ADVANCE_TO_INDIVIDUAL_ANALYSIS":
        reason_code = "RESIDUAL_MISPRICING_HIGH"
    else:
        reason_code = "RESIDUAL_MISPRICING_BELOW_ADVANCE_THRESHOLD"

    return {"reason_code": reason_code, "decision_inputs": inputs}


def reconciliation_status(
    candidate: dict[str, Any],
    assessment: dict[str, Any],
    decision: str | None = None,
) -> str:
    """Compare whether independent analysis confirms escalation of a screened candidate."""
    signals = candidate["mechanical_signals"]
    if signals.get("hard_exclusions") or signals.get("quality_gate") != "passed":
        return "NOT_COMPARABLE_HARD_GATE"

    resolved = decision or integrate(candidate, assessment)
    if resolved == "INSUFFICIENT_EVIDENCE":
        return "INSUFFICIENT_EVIDENCE"
    if resolved == "ADVANCE_TO_INDIVIDUAL_ANALYSIS":
        return "AGREE"
    if resolved == "MONITOR":
        return "PARTIALLY_AGREE"
    return "DISAGREE"


def validate_ledger(records: list[dict[str, Any]]) -> None:
    keys = [(r["generation_id"], r["candidate_id"]) for r in records]
    if len(keys) != len(set(keys)):
        raise AnalysisIntegrityError("duplicate ledger record")
    for record in records:
        if set(record["decisions"]) != {"mechanical", "ai_independent", "integrated"}:
            raise AnalysisIntegrityError("decision layers are not separated")
        outcome = record.get("outcome")
        if (
            outcome
            and outcome["status"] == "not_matured"
            and any(outcome.get(key) is not None for key in ("return_5d", "return_21d", "return_63d", "return_126d"))
        ):
            raise AnalysisIntegrityError("future leakage in immature outcome")
