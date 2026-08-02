import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.ai_analysis import canonical_hash
from src.analysis_readback import AnalysisReadbackError, publish_split_readback, verify_split_readback


class AnalysisReadbackTests(unittest.TestCase):
    def make_bundle(self, count=5):
        candidate_ids = [f"cand_{index}" for index in range(count)]
        return {
            "analysis_contract_version": "3.0",
            "generation_id": "gen_" + "a" * 64,
            "candidate_set_id": "cset_" + "b" * 64,
            "ai_artifact_path": "docs/analysis/v3/example/ai-judgments/ai.json",
            "ai_artifact_sha256": "c" * 64,
            "reconciliation_projection": [
                {"candidate_id": candidate_id, "mechanical_signals": {"mechanical_rank": index + 1}}
                for index, candidate_id in enumerate(candidate_ids)
            ],
            "integrated_decisions": [
                {"candidate_id": candidate_id, "decision": "MONITOR"} for candidate_id in candidate_ids
            ],
            "exploratory_proposals": [],
            "blind_handoffs": [
                {"candidate_id": candidate_id, "handoff_id": f"blind_{index}"}
                for index, candidate_id in enumerate(candidate_ids)
            ],
            "reconciliation_handoffs": [
                {"candidate_id": candidate_id, "handoff_id": f"reconcile_{index}", "status": "active"}
                for index, candidate_id in enumerate(candidate_ids)
            ],
            "decision_ledger": [
                {"candidate_id": candidate_id, "generation_id": "gen_" + "a" * 64}
                for candidate_id in candidate_ids
            ],
            "outcomes": [{"candidate_id": candidate_id, "status": "not_matured"} for candidate_id in candidate_ids],
        }

    def write_result(self, root, bundle):
        bundle_id = "analysis_" + canonical_hash(bundle)
        path = root / "docs/analysis/v3" / bundle["generation_id"] / "sessions" / f"{bundle_id}.json"
        path.parent.mkdir(parents=True)
        data = (json.dumps(bundle, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
        path.write_bytes(data)
        return {
            "bundle_id": bundle_id,
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bundle": bundle,
        }

    def test_split_readback_is_bounded_reconstructable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.write_result(root, self.make_bundle())
            result.update(publish_split_readback(root, result))
            manifest = verify_split_readback(root, result)
            self.assertEqual(result["readback_part_count"], 3)
            self.assertEqual(manifest["candidate_count"], 5)
            self.assertEqual(len(manifest["candidate_parts"]), 3)
            self.assertLessEqual(max(len(part["candidate_ids"]) for part in manifest["candidate_parts"]), 2)
            self.assertEqual(publish_split_readback(root, result)["readback_manifest_sha256"], result["readback_manifest_sha256"])

    def test_tampered_part_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.write_result(root, self.make_bundle(3))
            result.update(publish_split_readback(root, result))
            manifest = verify_split_readback(root, result)
            part_path = root / manifest["candidate_parts"][0]["path"]
            part_path.write_text("{}\n", encoding="utf-8")
            with self.assertRaises(AnalysisReadbackError):
                verify_split_readback(root, result)


if __name__ == "__main__":
    unittest.main()
