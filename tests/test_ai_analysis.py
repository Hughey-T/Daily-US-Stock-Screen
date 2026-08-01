import json
import unittest
from copy import deepcopy
from pathlib import Path

from src.ai_analysis import (
    AnalysisIntegrityError,
    advance_session,
    blind_projection,
    candidate_set_id,
    integrate,
    reconcile,
    strict_json_loads,
    validate_assessments,
    validate_ledger,
)

GEN = "gen_" + "a" * 64
CANDIDATE = {
    "candidate_id": "cand_a",
    "generation_id": GEN,
    "security_identity": "sec_a",
    "ticker": "AAA",
    "route": "event_anomaly",
    "facts": {"price": 10},
    "measured_metrics": {"return_21d": 0.2},
    "evidence_refs": ["ev1"],
    "evidence_cutoff": "2026-07-31T20:00:00Z",
    "mechanical_signals": {
        "mechanical_rank": 1,
        "selection_bucket": "event",
        "mechanical_priority": "high",
        "quality_gate": "passed",
        "hard_exclusions": [],
    },
}


def likelihood(value=0.5):
    return {"value": value, "basis": "primary source and measured move", "status": "estimated"}


def assessment():
    return {
        "candidate_id": "cand_a",
        "generation_id": GEN,
        "analysis_as_of": "2026-07-31T21:00:00Z",
        "evidence_cutoff": "2026-07-31T20:00:00Z",
        "ai_assessment_status": "assessed",
        "observed_move_summary": "moved",
        "primary_hypothesis": "demand",
        "causal_chain": ["event", "expectations", "KPI", "price", "gap"],
        "alternative_hypotheses": ["sector"],
        "counterevidence": ["valuation"],
        "data_anomaly_likelihood": likelihood(0.1),
        "event_explained_likelihood": likelihood(0.4),
        "residual_mispricing_likelihood": likelihood(0.8),
        "residual_mispricing_direction": "upside",
        "expected_resolution_horizon": "months",
        "catalysts": ["filing"],
        "invalidation_conditions": ["KPI misses"],
        "uncertainties": ["demand"],
        "additional_research": ["10-Q"],
        "downstream_suitability": "suitable",
        "mechanical_agreement": "AGREE",
        "confidence": likelihood(0.7),
        "evidence_refs": ["ev1"],
    }


def artifact():
    return {
        "analysis_contract_version": "3.0",
        "generation_id": GEN,
        "candidate_set_id": candidate_set_id([CANDIDATE]),
        "evidence_cutoff": "2026-07-31T20:00:00Z",
        "assessments": [assessment()],
    }


class AnalysisContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(Path("schemas/v3.0/ai-assessment.schema.json").read_text())

    def test_initial_eight_and_update_three_e2e(self):
        state = {"generation_id": GEN, "mode": "initial", "phase": 1}
        for expected in range(2, 9):
            state = advance_session(state, "次", GEN)
            self.assertEqual(state["phase"], expected)
        state = advance_session(state, "更新", GEN)
        self.assertEqual((state["mode"], state["phase"]), ("update", 1))
        state = advance_session(state, "次", GEN)
        state = advance_session(state, "次", GEN)
        self.assertEqual(state["phase"], 3)
        with self.assertRaises(AnalysisIntegrityError):
            advance_session(state, "次", GEN)

    def test_exact_controls_only(self):
        state = {"generation_id": GEN, "mode": "initial", "phase": 1}
        for command in (" 次", "次。", "文章中の次", "update", "更新してください"):
            with self.assertRaises(AnalysisIntegrityError):
                advance_session(state, command, GEN)

    def test_update_requires_completion(self):
        with self.assertRaises(AnalysisIntegrityError):
            advance_session({"generation_id": GEN, "mode": "initial", "phase": 7}, "更新", GEN)

    def test_mixed_generation_rejected(self):
        with self.assertRaises(AnalysisIntegrityError):
            advance_session({"generation_id": GEN, "mode": "initial", "phase": 1}, "次", "gen_" + "b" * 64)

    def test_blind_projection_hides_rank(self):
        projected = blind_projection(CANDIDATE)
        rendered = json.dumps(projected)
        for key in ("mechanical_rank", "selection_bucket", "integrated_decision"):
            self.assertNotIn(key, rendered)

    def test_candidate_identity_changes_hash(self):
        changed = deepcopy(CANDIDATE)
        changed["ticker"] = "BBB"
        self.assertNotEqual(candidate_set_id([CANDIDATE]), candidate_set_id([changed]))

    def test_duplicate_candidate_rejected(self):
        with self.assertRaises(AnalysisIntegrityError):
            candidate_set_id([CANDIDATE, CANDIDATE])

    def test_valid_assessment_and_lock(self):
        payload = artifact()
        digest = validate_assessments(payload, [CANDIDATE], self.schema)
        payload["artifact_hash"] = digest
        payload["locked_at"] = "2026-07-31T21:01:00Z"
        self.assertEqual(reconcile([CANDIDATE], payload, digest)[0]["mechanical_signals"]["mechanical_rank"], 1)

    def test_reconciliation_before_lock_rejected(self):
        with self.assertRaises(AnalysisIntegrityError):
            reconcile([CANDIDATE], artifact(), "wrong")

    def test_ai_coverage_missing_or_extra(self):
        for assessments in ([], [assessment(), {**assessment(), "candidate_id": "unknown"}]):
            value = artifact()
            value["assessments"] = assessments
            with self.assertRaises(AnalysisIntegrityError):
                validate_assessments(value, [CANDIDATE], self.schema)

    def test_unknown_duplicate_or_missing_evidence(self):
        variants = [["unknown"], ["ev1", "ev1"], []]
        for refs in variants:
            value = artifact()
            value["assessments"][0]["evidence_refs"] = refs
            with self.assertRaises(AnalysisIntegrityError):
                validate_assessments(value, [CANDIDATE], self.schema)

    def test_probability_and_enum_schema_rejected(self):
        for field, value in (("confidence", likelihood(1.1)), ("mechanical_agreement", "MAYBE")):
            payload = artifact()
            payload["assessments"][0][field] = value
            with self.assertRaises(AnalysisIntegrityError):
                validate_assessments(payload, [CANDIDATE], self.schema)

    def test_future_timing_rejected(self):
        payload = artifact()
        payload["assessments"][0]["analysis_as_of"] = "2026-07-31T19:00:00Z"
        with self.assertRaises(AnalysisIntegrityError):
            validate_assessments(payload, [CANDIDATE], self.schema)

    def test_hard_gate_cannot_be_overridden(self):
        candidate = deepcopy(CANDIDATE)
        candidate["mechanical_signals"]["hard_exclusions"] = ["DATA_ANOMALY"]
        self.assertEqual(integrate(candidate, assessment()), "REJECT_DATA_ANOMALY")

    def test_integration_outcomes(self):
        self.assertEqual(integrate(CANDIDATE, assessment()), "ADVANCE_TO_INDIVIDUAL_ANALYSIS")
        ai = assessment()
        ai["ai_assessment_status"] = "insufficient_evidence"
        self.assertEqual(integrate(CANDIDATE, ai), "INSUFFICIENT_EVIDENCE")
        ai = assessment()
        ai["data_anomaly_likelihood"] = likelihood(0.8)
        self.assertEqual(integrate(CANDIDATE, ai), "REJECT_DATA_ANOMALY")
        ai = assessment()
        ai["event_explained_likelihood"] = likelihood(0.9)
        ai["residual_mispricing_likelihood"] = likelihood(0.2)
        self.assertEqual(integrate(CANDIDATE, ai), "REJECT_EXPLAINED_MOVE")
        ai = assessment()
        ai["residual_mispricing_likelihood"] = likelihood(0.5)
        self.assertEqual(integrate(CANDIDATE, ai), "MONITOR")

    def test_strict_json(self):
        self.assertEqual(strict_json_loads(b'{"a":1}'), {"a": 1})
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}', b"\xff"):
            with self.assertRaises((AnalysisIntegrityError, ValueError)):
                strict_json_loads(raw)

    def test_ledger_layers_uniqueness_and_maturity(self):
        base = {
            "generation_id": GEN,
            "candidate_id": "cand_a",
            "decisions": {
                "mechanical": "selected",
                "ai_independent": "advance",
                "integrated": "ADVANCE_TO_INDIVIDUAL_ANALYSIS",
            },
            "outcome": {
                "status": "not_matured",
                "return_5d": None,
                "return_21d": None,
                "return_63d": None,
                "return_126d": None,
            },
        }
        validate_ledger([base])
        with self.assertRaises(AnalysisIntegrityError):
            validate_ledger([base, base])
        leaked = deepcopy(base)
        leaked["outcome"]["return_5d"] = 0.1
        with self.assertRaises(AnalysisIntegrityError):
            validate_ledger([leaked])


if __name__ == "__main__":
    unittest.main()
