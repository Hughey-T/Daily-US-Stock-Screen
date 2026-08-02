import json
import os
import shutil
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from src.ai_analysis import canonical_hash
from src.analysis_runtime import candidates_from_snapshot, supersede_handoffs

SOURCE = Path("docs/generations/2026-07-31/gen_8dea912301f28c83cb7f75ad22804fb5f865f3c5d236e97791acb13ab701975d")


def likelihood(value):
    return {"value": value, "basis": "cutoff-eligible primary evidence", "status": "estimated"}


class ProductionRuntimeE2E(unittest.TestCase):
    def test_custom_gpt_write_read_replay_reconcile_and_update_supersession(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / SOURCE
            destination.parent.mkdir(parents=True)
            shutil.copytree(SOURCE, destination)
            (root / "schemas/v3.0").mkdir(parents=True)
            shutil.copy("schemas/v3.0/ai-assessment.schema.json", root / "schemas/v3.0/ai-assessment.schema.json")
            shutil.copy(
                "schemas/v3.0/analysis-write-request.schema.json",
                root / "schemas/v3.0/analysis-write-request.schema.json",
            )
            env = {**os.environ, "ANALYSIS_REPOSITORY_ROOT": str(root)}
            cli = ["python", "scripts/process_analysis_request.py"]
            relative_snapshot = (SOURCE / "snapshot.json").as_posix()
            subprocess.run(
                cli + ["publish-blind", "--snapshot", relative_snapshot], check=True, env=env, capture_output=True
            )
            blind = json.loads((root / "docs/analysis/v3" / SOURCE.name / "blind-projection.json").read_text())
            self.assertTrue(blind["candidates"])
            self.assertNotIn("mechanical_rank", json.dumps(blind))

            _, candidates = candidates_from_snapshot(destination / "snapshot.json")
            evidence = [
                {
                    "evidence_id": f"ev_{index}",
                    "candidate_id": candidate["candidate_id"],
                    "published_at": "2026-07-31T19:00:00Z",
                    "url": "https://example.test/primary",
                }
                for index, candidate in enumerate(candidates)
            ]
            assessments = []
            for index, candidate in enumerate(candidates):
                assessments.append(
                    {
                        "candidate_id": candidate["candidate_id"],
                        "generation_id": SOURCE.name,
                        "analysis_as_of": "2026-07-31T21:00:00Z",
                        "evidence_cutoff": "2026-07-31T20:00:00Z",
                        "ai_assessment_status": "assessed",
                        "observed_move_summary": "observed move",
                        "primary_hypothesis": "expectations changed",
                        "causal_chain": ["event", "expectations", "KPI", "price", "gap"],
                        "alternative_hypotheses": ["sector"],
                        "counterevidence": ["prior expectations"],
                        "data_anomaly_likelihood": likelihood(0.1),
                        "event_explained_likelihood": likelihood(0.4),
                        "residual_mispricing_likelihood": likelihood(0.7),
                        "residual_mispricing_direction": "upside",
                        "expected_resolution_horizon": "months",
                        "catalysts": ["filing"],
                        "invalidation_conditions": ["KPI miss"],
                        "uncertainties": ["demand"],
                        "additional_research": ["filing review"],
                        "downstream_suitability": "advance",
                        "mechanical_agreement": "INSUFFICIENT_EVIDENCE",
                        "confidence": likelihood(0.6),
                        "evidence_refs": [f"ev_{index}"],
                    }
                )
            artifact = {
                "analysis_contract_version": "3.0",
                "generation_id": SOURCE.name,
                "candidate_set_id": blind["candidate_set_id"],
                "evidence_cutoff": "2026-07-31T20:00:00Z",
                "assessments": assessments,
            }
            request = {
                "operation": "persist_ai_assessment_v3",
                "nonce": str(uuid.uuid4()),
                "repository": "Hughey-T/Daily-US-Stock-Screen",
                "source_snapshot_path": relative_snapshot,
                "submitted_at": "2026-07-31T21:01:00Z",
                "artifact": artifact,
                "evidence_registry": evidence,
            }
            request["request_id"] = "analysis_" + canonical_hash([artifact, evidence])
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request))
            subprocess.run(
                cli + ["persist-assessment", "--snapshot", relative_snapshot, "--request", str(request_path)],
                check=True,
                env=env,
                capture_output=True,
            )
            ledger = json.loads((root / "docs/analysis/v3/write-ledger.json").read_text())
            self.assertEqual(len(ledger["requests"]), 1)
            bundle_path = root / ledger["requests"][0]["path"]
            bundle = json.loads(bundle_path.read_text())
            self.assertEqual(len(bundle["reconciliation_projection"]), len(candidates))
            self.assertEqual(len(bundle["decision_ledger"]), len(candidates))
            self.assertEqual(len(bundle["blind_handoffs"]), len(candidates))
            self.assertTrue(all(item["comparison_status"] == "AGREE" for item in bundle["reconciliation_projection"]))
            self.assertTrue(
                all(
                    item["integration_basis"]["reason_code"] == "RESIDUAL_MISPRICING_HIGH"
                    for item in bundle["integrated_decisions"]
                )
            )
            self.assertTrue(
                all(item["decision"] == "ADVANCE_TO_INDIVIDUAL_ANALYSIS" for item in bundle["integrated_decisions"])
            )
            self.assertNotIn(
                "INSUFFICIENT_EVIDENCE",
                {item["comparison_status"] for item in bundle["reconciliation_projection"]},
            )

            subprocess.run(
                cli + ["persist-assessment", "--snapshot", relative_snapshot, "--request", str(request_path)],
                check=True,
                env=env,
                capture_output=True,
            )
            self.assertEqual(len(json.loads((root / "docs/analysis/v3/write-ledger.json").read_text())["requests"]), 1)
            modified = json.loads(request_path.read_text())
            modified["artifact"]["assessments"][0]["primary_hypothesis"] = "rewritten"
            request_path.write_text(json.dumps(modified))
            failed = subprocess.run(
                cli + ["persist-assessment", "--snapshot", relative_snapshot, "--request", str(request_path)],
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            superseded = supersede_handoffs(bundle, "gen_" + "b" * 64)
            self.assertTrue(all(item["status"] == "superseded" for item in superseded["reconciliation_handoffs"]))


if __name__ == "__main__":
    unittest.main()
