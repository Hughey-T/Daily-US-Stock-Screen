"""Size-bounded immutable readback projection for Contract 3.0 bundles."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from src.ai_analysis import canonical_hash, strict_json_loads

CANDIDATE_SECTIONS = (
    "reconciliation_projection",
    "integrated_decisions",
    "blind_handoffs",
    "reconciliation_handoffs",
    "decision_ledger",
    "outcomes",
)
READBACK_CHUNK_SIZE = 2


class AnalysisReadbackError(ValueError):
    """A split readback projection is incomplete, unsafe, or inconsistent."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()


def _write_new(path: Path, value: Any) -> tuple[str, int]:
    data = _json_bytes(value)
    if path.exists():
        if path.read_bytes() != data:
            raise AnalysisReadbackError("IMMUTABLE_READBACK_CONFLICT")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return hashlib.sha256(data).hexdigest(), len(data)


def _candidate_map(items: Any, section: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise AnalysisReadbackError(f"READBACK_SECTION_NOT_ARRAY:{section}")
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("candidate_id"), str):
            raise AnalysisReadbackError(f"READBACK_CANDIDATE_INVALID:{section}")
        candidate_id = item["candidate_id"]
        if candidate_id in indexed:
            raise AnalysisReadbackError(f"READBACK_CANDIDATE_DUPLICATE:{section}")
        indexed[candidate_id] = item
    return indexed


def _safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    analysis_root = (root / "docs/analysis/v3").resolve()
    if analysis_root not in path.parents or not path.is_file():
        raise AnalysisReadbackError("UNSAFE_READBACK_PATH")
    return path


def _read_descriptor(root: Path, descriptor: dict[str, Any]) -> dict[str, Any]:
    required = {"path", "raw_sha256", "canonical_sha256", "byte_length"}
    if not required.issubset(descriptor):
        raise AnalysisReadbackError("READBACK_DESCRIPTOR_INCOMPLETE")
    path = _safe_path(root, descriptor["path"])
    data = path.read_bytes()
    if len(data) != descriptor["byte_length"]:
        raise AnalysisReadbackError("READBACK_LENGTH_MISMATCH")
    if hashlib.sha256(data).hexdigest() != descriptor["raw_sha256"]:
        raise AnalysisReadbackError("READBACK_RAW_HASH_MISMATCH")
    value = strict_json_loads(data)
    if not isinstance(value, dict):
        raise AnalysisReadbackError("READBACK_OBJECT_REQUIRED")
    if canonical_hash(value) != descriptor["canonical_sha256"]:
        raise AnalysisReadbackError("READBACK_CANONICAL_HASH_MISMATCH")
    return value


def publish_split_readback(root: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Write a small manifest plus size-bounded parts for one immutable bundle."""
    bundle = result.get("bundle")
    if not isinstance(bundle, dict):
        bundle = strict_json_loads(_safe_path(root, result["path"]).read_bytes())
    bundle = cast(dict[str, Any], bundle)
    bundle_id = result.get("bundle_id") or Path(result["path"]).stem
    if not isinstance(bundle_id, str) or not bundle_id.startswith("analysis_"):
        raise AnalysisReadbackError("READBACK_BUNDLE_ID_INVALID")

    section_maps = {section: _candidate_map(bundle.get(section), section) for section in CANDIDATE_SECTIONS}
    candidate_ids = list(section_maps["integrated_decisions"])
    expected = set(candidate_ids)
    if not candidate_ids or any(set(indexed) != expected for indexed in section_maps.values()):
        raise AnalysisReadbackError("READBACK_CANDIDATE_COVERAGE_MISMATCH")

    base = _safe_path(root, result["path"]).parent
    part_count = (len(candidate_ids) + READBACK_CHUNK_SIZE - 1) // READBACK_CHUNK_SIZE
    part_descriptors = []
    for offset in range(0, len(candidate_ids), READBACK_CHUNK_SIZE):
        part_number = offset // READBACK_CHUNK_SIZE + 1
        chunk_ids = candidate_ids[offset : offset + READBACK_CHUNK_SIZE]
        part = {
            "artifact_type": "ANALYSIS_BUNDLE_READBACK_PART",
            "analysis_contract_version": bundle["analysis_contract_version"],
            "generation_id": bundle["generation_id"],
            "candidate_set_id": bundle["candidate_set_id"],
            "bundle_id": bundle_id,
            "part_number": part_number,
            "part_count": part_count,
            "candidate_ids": chunk_ids,
            **{
                section: [section_maps[section][candidate_id] for candidate_id in chunk_ids]
                for section in CANDIDATE_SECTIONS
            },
        }
        part_path = base / f"{bundle_id}--readback-part-{part_number:02d}-of-{part_count:02d}.json"
        raw_sha, byte_length = _write_new(part_path, part)
        part_descriptors.append(
            {
                "part_number": part_number,
                "candidate_ids": chunk_ids,
                "path": part_path.relative_to(root).as_posix(),
                "raw_sha256": raw_sha,
                "canonical_sha256": canonical_hash(part),
                "byte_length": byte_length,
            }
        )

    global_fields = {key: value for key, value in bundle.items() if key not in CANDIDATE_SECTIONS}
    global_part = {
        "artifact_type": "ANALYSIS_BUNDLE_READBACK_GLOBAL",
        "analysis_contract_version": bundle["analysis_contract_version"],
        "generation_id": bundle["generation_id"],
        "candidate_set_id": bundle["candidate_set_id"],
        "bundle_id": bundle_id,
        "global_fields": global_fields,
    }
    global_path = base / f"{bundle_id}--readback-global.json"
    global_raw_sha, global_byte_length = _write_new(global_path, global_part)
    global_descriptor = {
        "path": global_path.relative_to(root).as_posix(),
        "raw_sha256": global_raw_sha,
        "canonical_sha256": canonical_hash(global_part),
        "byte_length": global_byte_length,
    }

    manifest = {
        "artifact_type": "ANALYSIS_BUNDLE_READBACK_MANIFEST",
        "analysis_contract_version": bundle["analysis_contract_version"],
        "generation_id": bundle["generation_id"],
        "candidate_set_id": bundle["candidate_set_id"],
        "bundle_id": bundle_id,
        "bundle_path": result["path"],
        "bundle_raw_sha256": result["sha256"],
        "bundle_canonical_sha256": canonical_hash(bundle),
        "candidate_count": len(candidate_ids),
        "candidate_ids": candidate_ids,
        "candidate_section_keys": list(CANDIDATE_SECTIONS),
        "chunk_size": READBACK_CHUNK_SIZE,
        "global_part": global_descriptor,
        "candidate_parts": part_descriptors,
    }
    manifest_path = base / f"{bundle_id}--readback-manifest.json"
    manifest_raw_sha, manifest_byte_length = _write_new(manifest_path, manifest)
    return {
        "readback_manifest_path": manifest_path.relative_to(root).as_posix(),
        "readback_manifest_sha256": manifest_raw_sha,
        "readback_manifest_canonical_sha256": canonical_hash(manifest),
        "readback_manifest_byte_length": manifest_byte_length,
        "readback_part_count": part_count,
    }


def verify_split_readback(root: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Verify every part and reconstruct the exact stored bundle object."""
    bundle_path = _safe_path(root, result["path"])
    bundle_bytes = bundle_path.read_bytes()
    if hashlib.sha256(bundle_bytes).hexdigest() != result["sha256"]:
        raise AnalysisReadbackError("READBACK_BUNDLE_RAW_HASH_MISMATCH")
    bundle = strict_json_loads(bundle_bytes)
    if not isinstance(bundle, dict):
        raise AnalysisReadbackError("READBACK_BUNDLE_OBJECT_REQUIRED")

    manifest_descriptor = {
        "path": result["readback_manifest_path"],
        "raw_sha256": result["readback_manifest_sha256"],
        "canonical_sha256": result["readback_manifest_canonical_sha256"],
        "byte_length": result["readback_manifest_byte_length"],
    }
    manifest = _read_descriptor(root, manifest_descriptor)
    if manifest["bundle_path"] != result["path"] or manifest["bundle_raw_sha256"] != result["sha256"]:
        raise AnalysisReadbackError("READBACK_MANIFEST_BUNDLE_MISMATCH")
    if manifest["bundle_canonical_sha256"] != canonical_hash(bundle):
        raise AnalysisReadbackError("READBACK_BUNDLE_CANONICAL_HASH_MISMATCH")
    if manifest["candidate_section_keys"] != list(CANDIDATE_SECTIONS):
        raise AnalysisReadbackError("READBACK_SECTION_INVENTORY_MISMATCH")
    if manifest["chunk_size"] != READBACK_CHUNK_SIZE:
        raise AnalysisReadbackError("READBACK_CHUNK_SIZE_MISMATCH")
    if len(manifest["candidate_parts"]) != result["readback_part_count"]:
        raise AnalysisReadbackError("READBACK_PART_COUNT_MISMATCH")

    global_part = _read_descriptor(root, manifest["global_part"])
    if (
        global_part["bundle_id"] != manifest["bundle_id"]
        or global_part["generation_id"] != manifest["generation_id"]
        or global_part["candidate_set_id"] != manifest["candidate_set_id"]
    ):
        raise AnalysisReadbackError("READBACK_GLOBAL_IDENTITY_MISMATCH")
    reconstructed = dict(global_part["global_fields"])
    for section in CANDIDATE_SECTIONS:
        reconstructed[section] = []

    observed_ids: list[str] = []
    for expected_number, descriptor in enumerate(manifest["candidate_parts"], start=1):
        if descriptor["part_number"] != expected_number:
            raise AnalysisReadbackError("READBACK_PART_SEQUENCE_MISMATCH")
        part = _read_descriptor(root, descriptor)
        if (
            part["bundle_id"] != manifest["bundle_id"]
            or part["generation_id"] != manifest["generation_id"]
            or part["candidate_set_id"] != manifest["candidate_set_id"]
            or part["part_number"] != expected_number
            or part["part_count"] != len(manifest["candidate_parts"])
            or part["candidate_ids"] != descriptor["candidate_ids"]
        ):
            raise AnalysisReadbackError("READBACK_PART_IDENTITY_MISMATCH")
        observed_ids.extend(part["candidate_ids"])
        for section in CANDIDATE_SECTIONS:
            values = part.get(section)
            if not isinstance(values, list) or [item.get("candidate_id") for item in values] != part["candidate_ids"]:
                raise AnalysisReadbackError(f"READBACK_PART_COVERAGE_MISMATCH:{section}")
            reconstructed[section].extend(values)

    if observed_ids != manifest["candidate_ids"] or len(observed_ids) != manifest["candidate_count"]:
        raise AnalysisReadbackError("READBACK_MANIFEST_COVERAGE_MISMATCH")
    if reconstructed != bundle:
        raise AnalysisReadbackError("READBACK_RECONSTRUCTION_MISMATCH")
    return manifest
