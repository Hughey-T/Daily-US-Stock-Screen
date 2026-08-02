"""Closed validation for Analysis Contract 3.0 Issue write requests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from src.ai_analysis import AnalysisIntegrityError, canonical_hash, strict_json_loads


class AnalysisRequestError(ValueError):
    """A Custom GPT analysis write request is not contract-valid."""


def _validate_schema(instance: Any, schema: dict[str, Any], label: str) -> None:
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(instance),
        key=lambda error: list(error.path),
    )
    if errors:
        path = "/".join(str(part) for part in errors[0].path) or "<root>"
        raise AnalysisRequestError(f"{label}_SCHEMA_INVALID:{path}:{errors[0].message}")


def validate_analysis_write_request(
    root: Path,
    snapshot_path: Path,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Validate the exact Issue envelope, nested AI artifact and identities."""

    request_schema = strict_json_loads((root / "schemas/v3.0/analysis-write-request.schema.json").read_bytes())
    assessment_schema = strict_json_loads((root / "schemas/v3.0/ai-assessment.schema.json").read_bytes())
    _validate_schema(request, request_schema, "REQUEST")
    _validate_schema(request["artifact"], assessment_schema, "ARTIFACT")

    resolved_root = root.resolve()
    resolved_snapshot = snapshot_path.resolve()
    if resolved_root not in resolved_snapshot.parents:
        raise AnalysisRequestError("SNAPSHOT_ESCAPES_REPOSITORY")
    requested_snapshot = (resolved_root / request["source_snapshot_path"]).resolve()
    if requested_snapshot != resolved_snapshot:
        raise AnalysisRequestError("SOURCE_SNAPSHOT_PATH_MISMATCH")

    expected_request_id = "analysis_" + canonical_hash([request["artifact"], request["evidence_registry"]])
    if request["request_id"] != expected_request_id:
        raise AnalysisRequestError("REQUEST_ID_MISMATCH")

    evidence_ids = [item["evidence_id"] for item in request["evidence_registry"]]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise AnalysisRequestError("DUPLICATE_EVIDENCE_ID")

    if request["artifact"]["generation_id"] not in request["source_snapshot_path"]:
        raise AnalysisRequestError("GENERATION_SNAPSHOT_IDENTITY_MISMATCH")

    return request


__all__ = [
    "AnalysisIntegrityError",
    "AnalysisRequestError",
    "validate_analysis_write_request",
]
