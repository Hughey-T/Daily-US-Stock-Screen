import json
import unittest
import uuid
from copy import deepcopy
from pathlib import Path

import yaml

from src.ai_analysis import canonical_hash
from src.analysis_request import AnalysisRequestError, validate_analysis_write_request


GENERATION_ID = "gen_" + "a" * 64
SNAPSHOT_PATH = Path(f"docs/generations/2026-07-31/{GENERATION_ID}/snapshot.json")


def likelihood_not_evaluable():
    return {"value": None, "basis": "no cutoff-eligible external evidence", "status": "not_evaluable"}


def valid_artifact():
    return {
        "analysis_contract_version": "3.0",
        "generation_id": GENERATION_ID,
        "candidate_set_id": "cset_" + "b" * 64,
        "evidence_cutoff": "2026-07-31T20:00:00Z",
        "assessments": [
            {
                "candidate_id": "candidate-a",
                "generation_id": GENERATION_ID,
                "analysis_as_of": "2026-07-31T21:00:00Z",
                "evidence_cutoff": "2026-07-31T20:00:00Z",
                "ai_assessment_status": "insufficient_evidence",
                "observed_move_summary": "observed move without external evidence",
                "primary_hypothesis": "unverified expectations change",
                "causal_chain": ["event", "expectations", "KPI", "price", "gap"],
                "alternative_hypotheses": ["data anomaly"],
                "counterevidence": ["no external evidence"],
                "data_anomaly_likelihood": likelihood_not_evaluable(),
                "event_explained_likelihood": likelihood_not_evaluable(),
                "residual_mispricing_likelihood": likelihood_not_evaluable(),
                "residual_mispricing_direction": "not_evaluable",
                "expected_resolution_horizon": "unknown",
                "catalysts": ["primary-source verification"],
                "invalidation_conditions": ["mechanical adjustment confirmed"],
                "uncertainties": ["event identity"],
                "additional_research": ["verify primary sources"],
                "downstream_suitability": "conditional",
                "mechanical_agreement": "INSUFFICIENT_EVIDENCE",
                "confidence": likelihood_not_evaluable(),
                "evidence_refs": [],
            }
        ],
    }


def valid_request():
    artifact = valid_artifact()
    evidence_registry = []
    request_id = "analysis_" + canonical_hash([artifact, evidence_registry])
    return {
        "operation": "persist_ai_assessment_v3",
        "request_id": request_id,
        "nonce": str(uuid.uuid4()),
        "repository": "Hughey-T/Daily-US-Stock-Screen",
        "source_snapshot_path": SNAPSHOT_PATH.as_posix(),
        "submitted_at": "2026-07-31T21:01:00Z",
        "artifact": artifact,
        "evidence_registry": evidence_registry,
    }


class AnalysisWriteRequestTests(unittest.TestCase):
    def test_closed_canonical_request_is_accepted(self):
        request = valid_request()
        self.assertIs(
            validate_analysis_write_request(Path("."), SNAPSHOT_PATH, request),
            request,
        )

    def test_alias_envelope_and_ai_judgments_are_rejected(self):
        request = valid_request()
        request["request_contract_version"] = "3.0"
        with self.assertRaises(AnalysisRequestError):
            validate_analysis_write_request(Path("."), SNAPSHOT_PATH, request)

        request = valid_request()
        request["artifact"]["AI_JUDGMENTS"] = request["artifact"].pop("assessments")
        request["request_id"] = "analysis_" + canonical_hash([request["artifact"], []])
        with self.assertRaises(AnalysisRequestError):
            validate_analysis_write_request(Path("."), SNAPSHOT_PATH, request)

    def test_request_id_and_snapshot_identity_are_recomputed(self):
        request = valid_request()
        request["request_id"] = "analysis_" + "0" * 64
        with self.assertRaisesRegex(AnalysisRequestError, "REQUEST_ID_MISMATCH"):
            validate_analysis_write_request(Path("."), SNAPSHOT_PATH, request)

        request = valid_request()
        other = Path(f"docs/generations/2026-07-30/{GENERATION_ID}/snapshot.json")
        with self.assertRaisesRegex(AnalysisRequestError, "SOURCE_SNAPSHOT_PATH_MISMATCH"):
            validate_analysis_write_request(Path("."), other, request)

    def test_issue_workflows_are_prefix_partitioned(self):
        legacy_path = Path(".github/workflows/process-gpt-write-request.yml")
        analysis_path = Path(".github/workflows/process-analysis-request.yml")
        legacy = legacy_path.read_text(encoding="utf-8")
        analysis = analysis_path.read_text(encoding="utf-8")
        yaml.safe_load(legacy)
        yaml.safe_load(analysis)
        self.assertIn("startsWith(github.event.issue.title, '[GPT-WRITE-V2] ')", legacy)
        self.assertIn("startsWith(github.event.issue.title, '[GPT-ANALYSIS-V3] ')", analysis)
        self.assertNotIn("[GPT-ANALYSIS-V3]", legacy)

    def test_request_schema_documents_forbidden_aliases(self):
        schema = json.loads(Path("schemas/v3.0/analysis-write-request.schema.json").read_text(encoding="utf-8"))
        self.assertIn("AI_JUDGMENTS", schema["x-forbidden-aliases"])
        self.assertEqual(schema["x-title-rule"], "[GPT-ANALYSIS-V3] {request_id}")


if __name__ == "__main__":
    unittest.main()
