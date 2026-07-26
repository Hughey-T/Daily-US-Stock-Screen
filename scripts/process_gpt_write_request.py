#!/usr/bin/env python3
"""Process a trusted ``issues.opened`` event without shell interpolation."""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.github_persistence import verify_github_prediction
from src.write_request import WriteRequestError, finalize_receipt, prepare_write, safe_failure

STATE = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "gpt-write-state.json"
REPOSITORY = "Hughey-T/Daily-US-Stock-Screen"


def _event(path: Path) -> dict:
    return json.loads(path.read_text())


def _write_state(value: dict) -> None:
    STATE.write_text(json.dumps(value, indent=2) + "\n")


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Daily-US-Stock-Screen-writer/2.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def prepare(event_path: Path) -> None:
    event = _event(event_path)
    try:
        result = prepare_write(event, ROOT)
    except WriteRequestError as exc:
        title = str(event.get("issue", {}).get("title", ""))
        request_id = title.split()[-1] if title.startswith("[GPT-WRITE-V2] ") else None
        _write_state({"status": "failed", "issue_number": event.get("issue", {}).get("number"), "failure": safe_failure(request_id, exc.code)})
        raise SystemExit(2)
    receipt = result["receipt"]
    _write_state({"status": "replay" if result["replay"] else "prepared", "issue_number": event["issue"]["number"],
                  "request_id": receipt["request_id"], "receipt": receipt, "index_entry": result.get("index_entry")})


def remote_verify(commit_sha: str) -> None:
    state = json.loads(STATE.read_text())
    if state["status"] == "replay":
        return
    try:
        proof = verify_github_prediction(state["index_entry"], REPOSITORY, "main", commit_sha, _fetch)
        receipt = finalize_receipt(ROOT, state["request_id"], commit_sha, proof)
    except Exception:
        state.update({"status": "failed", "failure": safe_failure(state.get("request_id"), "REMOTE_VERIFICATION_FAILED")})
        _write_state(state)
        raise SystemExit(3)
    state.update({"status": "finalized", "proof": proof, "receipt": receipt})
    _write_state(state)


def verify_receipt(commit_sha: str) -> None:
    state = json.loads(STATE.read_text())
    if state["status"] == "replay":
        return
    path = f"docs/write-requests/receipts/{state['request_id']}.json"
    base = f"https://raw.githubusercontent.com/{REPOSITORY}"
    commit_bytes = _fetch(f"{base}/{commit_sha}/{path}")
    branch_bytes = _fetch(f"{base}/main/{path}")
    if commit_bytes != branch_bytes or json.loads(commit_bytes).get("write_request_status") != "integrity_verified":
        state.update({"status": "failed", "failure": safe_failure(state.get("request_id"), "RECEIPT_REMOTE_MISMATCH")})
        _write_state(state)
        raise SystemExit(4)
    state["status"] = "verified"
    _write_state(state)


def _github(method: str, path: str, payload: dict | None = None) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("workflow token unavailable")
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"https://api.github.com{path}", data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "Daily-US-Stock-Screen-writer/2.0"})
    with urllib.request.urlopen(req, timeout=30):
        pass


def report(success: bool) -> None:
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    number = state.get("issue_number")
    if not isinstance(number, int):
        return
    if success and state.get("status") in {"verified", "replay"}:
        receipt = state["receipt"]
        protocol = {key: receipt[key] for key in ("write_request_status", "request_id", "prediction_run_id", "prediction_path", "prediction_sha256", "index_path")}
        protocol.update({"receipt_path": f"docs/write-requests/receipts/{receipt['request_id']}.json",
                         "verified_commit_sha": receipt.get("verified_commit_sha"), "completed_at": receipt.get("completed_at")})
        body = "Prediction persistence was remotely integrity-verified.\n\n```json\n" + json.dumps(protocol, indent=2) + "\n```"
    else:
        protocol = state.get("failure") or safe_failure(state.get("request_id"), "WORKFLOW_FAILURE")
        body = "Prediction persistence failed safely; no saved state is claimed.\n\n```json\n" + json.dumps(protocol, indent=2) + "\n```"
    _github("POST", f"/repos/{REPOSITORY}/issues/{number}/comments", {"body": body})
    _github("PATCH", f"/repos/{REPOSITORY}/issues/{number}", {"state": "closed"})


def main() -> None:
    parser = argparse.ArgumentParser(); subs = parser.add_subparsers(dest="command", required=True)
    p = subs.add_parser("prepare"); p.add_argument("--event", type=Path, required=True)
    for name in ("remote-verify", "verify-receipt"):
        p = subs.add_parser(name); p.add_argument("--commit", required=True)
    subs.add_parser("report-success"); subs.add_parser("report-failure")
    args = parser.parse_args()
    if args.command == "prepare": prepare(args.event)
    elif args.command == "remote-verify": remote_verify(args.commit)
    elif args.command == "verify-receipt": verify_receipt(args.commit)
    elif args.command == "report-success": report(True)
    else: report(False)

if __name__ == "__main__": main()
