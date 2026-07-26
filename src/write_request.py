"""Trust boundary for Custom GPT issue write requests.

Only data from the immutable ``issues.opened`` event is accepted.  This module
never invokes a shell and never treats request values as repository paths.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator, FormatChecker

from src.integrity import verify_snapshot
from src.records import build_prediction_bundle, persist_prediction_bundle

REPOSITORY = "Hughey-T/Daily-US-Stock-Screen"
OWNER = "Hughey-T"
MAX_BODY_BYTES = 50 * 1024
TITLE_RE = re.compile(r"^\[GPT-WRITE-V2\] (gptw_[0-9a-f]{64})$")
PLACEHOLDER_RE = re.compile(r"\b(?:todo|tbd|placeholder|pending analysis|machine-generated record pending)\b", re.I)


class WriteRequestError(ValueError):
    def __init__(self, code: str, message: str = "request rejected"):
        super().__init__(message)
        self.code = code


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def request_id(generation_id: str, prompt_hash: str, research_hash: str) -> str:
    material = f"persist_prediction_v2|{generation_id}|{prompt_hash}|{research_hash}".encode()
    return "gptw_" + hashlib.sha256(material).hexdigest()


def _time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise WriteRequestError("INVALID_TIMESTAMP") from exc
    if parsed.tzinfo is None:
        raise WriteRequestError("INVALID_TIMESTAMP")
    return parsed.astimezone(timezone.utc)


def _safe_relative(path: str, prefix: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or not path.startswith(prefix):
        raise WriteRequestError("UNSAFE_SOURCE_PATH")
    return candidate


def load_ledger(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text()) if path.exists() else {"ledger_schema_version": "1.0", "requests": []}
    if set(data) != {"ledger_schema_version", "requests"} or data["ledger_schema_version"] != "1.0" or not isinstance(data["requests"], list):
        raise WriteRequestError("LEDGER_CORRUPT")
    ids, nonces = set(), set()
    for item in data["requests"]:
        if set(item) != {"request_id", "nonce", "payload_sha256", "receipt_path", "recorded_at"}:
            raise WriteRequestError("LEDGER_CORRUPT")
        if item["request_id"] in ids or item["nonce"] in nonces:
            raise WriteRequestError("LEDGER_DUPLICATE")
        ids.add(item["request_id"]); nonces.add(item["nonce"])
    return data


def _validate_replay(request: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any] | None:
    by_id = next((x for x in ledger["requests"] if x["request_id"] == request["request_id"]), None)
    if by_id:
        if by_id["payload_sha256"] != request["payload_sha256"]:
            raise WriteRequestError("REQUEST_ID_PAYLOAD_CONFLICT")
        return by_id
    if any(x["nonce"] == request["nonce"] for x in ledger["requests"]):
        raise WriteRequestError("NONCE_REUSED")
    return None


def validate_issue_event(event: dict[str, Any], root: Path, now: datetime | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """Authorize, validate and resolve an opened-event request against main bytes."""
    if event.get("action") != "opened" or event.get("repository", {}).get("full_name") != REPOSITORY:
        raise WriteRequestError("WRONG_REPOSITORY")
    issue = event.get("issue", {})
    if issue.get("user", {}).get("login") != OWNER or event.get("sender", {}).get("login") != OWNER:
        raise WriteRequestError("UNAUTHORIZED_ACTOR")
    if issue.get("author_association") != "OWNER":
        raise WriteRequestError("UNAUTHORIZED_ASSOCIATION")
    match = TITLE_RE.fullmatch(str(issue.get("title", "")))
    if not match:
        raise WriteRequestError("INVALID_TITLE")
    body = issue.get("body")
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise WriteRequestError("BODY_TOO_LARGE")
    try:
        request = json.loads(body, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (json.JSONDecodeError, ValueError) as exc:
        raise WriteRequestError("INVALID_JSON") from exc
    # The schema is trusted application code, not content from the request's
    # storage root (tests intentionally use isolated publication roots).
    schema_path = Path(__file__).resolve().parents[1] / "schemas/v2.0/gpt_write_request.schema.json"
    schema = json.loads(schema_path.read_text())
    errors = sorted(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(request), key=lambda e: list(e.path))
    if errors:
        raise WriteRequestError("SCHEMA_INVALID", errors[0].message)
    if request["request_id"] != match.group(1):
        raise WriteRequestError("TITLE_REQUEST_ID_MISMATCH")
    created, expires = _time(request["created_at"]), _time(request["expires_at"])
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires <= created or (expires-created).total_seconds() > 600:
        raise WriteRequestError("EXPIRY_WINDOW_INVALID")
    if current > expires:
        raise WriteRequestError("REQUEST_EXPIRED")
    if created > current:
        raise WriteRequestError("REQUEST_FROM_FUTURE")
    parsed_nonce = uuid.UUID(request["nonce"])
    if parsed_nonce.version != 4:
        raise WriteRequestError("INVALID_NONCE")
    actual_payload_hash = payload_hash(request["research_payload"])
    if actual_payload_hash != request["payload_sha256"]:
        raise WriteRequestError("PAYLOAD_HASH_MISMATCH")
    expected_id = request_id(request["source_generation_id"], request["prompt_hash"], actual_payload_hash)
    if request["request_id"] != expected_id:
        raise WriteRequestError("REQUEST_ID_MISMATCH")
    source_rel = _safe_relative(request["source_snapshot_path"], "docs/generations/")
    snapshot_path = (root / source_rel).resolve()
    if root.resolve() not in snapshot_path.parents or not snapshot_path.is_file():
        raise WriteRequestError("SOURCE_SNAPSHOT_NOT_FOUND")
    try:
        snapshot = verify_snapshot(snapshot_path)
    except Exception as exc:
        raise WriteRequestError("SOURCE_INTEGRITY_FAILURE") from exc
    snapshot["_path"] = source_rel.as_posix()
    if snapshot["snapshot_id"] != request["source_snapshot_id"] or snapshot["generation_id"] != request["source_generation_id"]:
        raise WriteRequestError("SOURCE_IDENTITY_MISMATCH")
    phase3 = json.loads((snapshot_path.parent / snapshot["files"]["phase3_artifact"]["path"]).read_text())
    entry_index_path = root / "docs/entry-resolutions/index.json"
    if not entry_index_path.exists():
        raise WriteRequestError("ENTRY_RESOLUTION_MISSING")
    entry_index = json.loads(entry_index_path.read_text())
    entry_meta = entry_index.get("generations", {}).get(snapshot["generation_id"])
    if not entry_meta:
        raise WriteRequestError("ENTRY_RESOLUTION_MISSING")
    entry_rel = _safe_relative(entry_meta["path"], "docs/entry-resolutions/")
    entry_path = root / entry_rel
    if not entry_path.is_file() or hashlib.sha256(entry_path.read_bytes()).hexdigest() != entry_meta["sha256"]:
        raise WriteRequestError("ENTRY_RESOLUTION_INTEGRITY")
    entry = json.loads(entry_path.read_text())
    entry_schema = json.loads((Path(__file__).resolve().parents[1] / "schemas/v2.0/entry_resolution.schema.json").read_text())
    entry_errors = list(Draft202012Validator(entry_schema, format_checker=FormatChecker()).iter_errors(entry))
    if entry_errors:
        raise WriteRequestError("ENTRY_RESOLUTION_SCHEMA")
    if entry.get("source_generation_id") != snapshot["generation_id"] or entry.get("source_snapshot_id") != snapshot["snapshot_id"]:
        raise WriteRequestError("ENTRY_RESOLUTION_IDENTITY")
    _validate_research(request["research_payload"], phase3, entry, snapshot)
    ledger = load_ledger(root / "docs/write-requests/ledger.json")
    replay = _validate_replay(request, ledger)
    return request, snapshot, phase3, {"payload": entry, "path": entry_rel.as_posix(), "sha256": entry_meta["sha256"]}, replay


def _validate_research(payload: dict[str, Any], phase3: dict[str, Any], entry: dict[str, Any], snapshot: dict[str, Any]) -> None:
    finals = {x["route_candidate_id"]: x for x in phase3["final_research_set"]}
    comparisons = {x["route_candidate_id"] for x in phase3.get("comparison_records", [])}
    resolved = {x["route_candidate_id"]: x for x in entry["records"]}
    seen = set()
    # The tradable cutoff is part of the separately immutable entry artifact;
    # older snapshot producers did not duplicate it at top level.
    entry_times = [row.get("first_tradable_at") for row in entry.get("records", []) if row.get("first_tradable_at")]
    cutoff = _time(min(entry_times)) if entry_times else _time(snapshot["market_data_cutoff_at"])
    for item in payload["candidates"]:
        rid = item["route_candidate_id"]
        if rid not in finals or rid in seen:
            raise WriteRequestError("UNKNOWN_OR_DUPLICATE_CANDIDATE")
        seen.add(rid)
        text = " ".join([item["primary_thesis"], item["falsifier"]])
        if PLACEHOLDER_RE.search(text):
            raise WriteRequestError("PLACEHOLDER_RESEARCH")
        for evidence in item["verified_facts"] + item["company_claims"]:
            if not evidence["information_cutoff_eligible"] or _time(evidence["published_at"]) > cutoff:
                raise WriteRequestError("FUTURE_INFORMATION_LEAKAGE")
        if item["forecast_applicable"]:
            if item["research_completeness"] != "complete" or not item["forecasts"]:
                raise WriteRequestError("FORECAST_RESEARCH_INCOMPLETE")
            if resolved.get(rid, {}).get("status") != "resolved":
                raise WriteRequestError("ENTRY_UNRESOLVED")
        elif item["forecasts"]:
            raise WriteRequestError("INAPPLICABLE_FORECAST_PRESENT")
        if item["research_status"] in {"unresolved", "incomplete"} and item["forecast_applicable"]:
            raise WriteRequestError("UNRESOLVED_FORECAST")
        if not set(item["comparison_references"]).issubset(comparisons):
            raise WriteRequestError("UNKNOWN_COMPARISON_REFERENCE")
    if seen != set(finals):
        raise WriteRequestError("RESEARCH_SET_INCOMPLETE")


def prepare_write(event: dict[str, Any], root: Path, now: datetime | None = None, validator: Callable | None = None) -> dict[str, Any]:
    request, snapshot, phase3, entry, replay = validate_issue_event(event, root, now)
    if replay:
        receipt = json.loads((root / replay["receipt_path"]).read_text())
        if receipt.get("write_request_status") != "integrity_verified":
            raise WriteRequestError("REPLAY_NOT_VERIFIED")
        return {"replay": True, "receipt": receipt}
    research = {item["route_candidate_id"]: item for item in request["research_payload"]["candidates"]}
    # records producer expects entity lookup; identities are always derived from Phase 3.
    research_by_entity = {row["entity_id"]: _producer_research(research[row["route_candidate_id"]]) for row in phase3["final_research_set"]}
    run_id = "gptv2_" + request["request_id"][5:]
    bundle = build_prediction_bundle(phase3, snapshot, research_by_entity, entry["payload"], entry["path"], entry["sha256"], run_id,
                                     request["prompt_version"], request["prompt_hash"], request["model_name"], os.environ.get("GITHUB_SHA", "trusted-main"))
    bundle["model_configuration"] = request["model_configuration"]
    if validator is None:
        from scripts.validate_v2_records import validate_prediction_data
        validator = validate_prediction_data
    prediction_path, local_receipt = persist_prediction_bundle(bundle, root, validator)
    index = json.loads((root / "docs/predictions/index-v2.json").read_text())
    index_entry = next(x for x in index["predictions"] if x["prediction_run_id"] == run_id)
    receipt_rel = f"docs/write-requests/receipts/{request['request_id']}.json"
    receipt = {"receipt_schema_version": "1.0", "write_request_status": "pending_remote_verification", "request_id": request["request_id"],
               "nonce": request["nonce"], "payload_sha256": request["payload_sha256"], "prediction_run_id": run_id,
               "prediction_path": prediction_path.relative_to(root).as_posix(), "prediction_sha256": index_entry["sha256"],
               "index_path": "docs/predictions/index-v2.json", "entry_resolution_id": entry["payload"]["entry_resolution_id"],
               "source_snapshot_id": snapshot["snapshot_id"], "record_count": index_entry["record_count"], "created_at": bundle["generated_at"]}
    receipt_path = root / receipt_rel; receipt_path.parent.mkdir(parents=True, exist_ok=True)
    if receipt_path.exists():
        raise WriteRequestError("RECEIPT_CONFLICT")
    receipt_path.write_text(json.dumps(receipt, indent=2, allow_nan=False) + "\n")
    ledger_path = root / "docs/write-requests/ledger.json"; ledger = load_ledger(ledger_path)
    ledger["requests"].append({"request_id": request["request_id"], "nonce": request["nonce"], "payload_sha256": request["payload_sha256"],
                               "receipt_path": receipt_rel, "recorded_at": datetime.now(timezone.utc).isoformat()})
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n")
    return {"replay": False, "receipt": receipt, "index_entry": index_entry, "local_receipt_state": local_receipt["state"]}


def _producer_research(item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item)
    result["invalidation_condition"] = result.pop("falsifier")
    result["estimated_fundamental_value_change"] = result.pop("valuation_range")
    return result


def finalize_receipt(root: Path, request_id_value: str, commit_sha: str, proof: dict[str, Any], completed_at: str | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"gptw_[0-9a-f]{64}", request_id_value) or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise WriteRequestError("INVALID_FINALIZATION_ID")
    path = root / "docs/write-requests/receipts" / f"{request_id_value}.json"
    receipt = json.loads(path.read_text())
    if receipt["write_request_status"] != "pending_remote_verification" or proof.get("state") != "integrity_verified":
        raise WriteRequestError("REMOTE_VERIFICATION_REQUIRED")
    receipt.update({"write_request_status": "integrity_verified", "verified_commit_sha": commit_sha,
                    "remote_prediction_sha256": proof["sha256"], "completed_at": completed_at or datetime.now(timezone.utc).isoformat()})
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def safe_failure(request_id_value: str | None, code: str, completed_at: str | None = None) -> dict[str, Any]:
    return {"write_request_status": "failed", "request_id": request_id_value or "unknown", "error_code": re.sub(r"[^A-Z0-9_]", "", code)[:64] or "INTERNAL_FAILURE",
            "retryable": code in {"PUSH_CONFLICT", "REMOTE_UNAVAILABLE"}, "completed_at": completed_at or datetime.now(timezone.utc).isoformat()}
