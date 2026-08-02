#!/usr/bin/env python3
"""Trusted production entry point for contract 3.0 Issue requests."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
ROOT = Path(os.environ.get("ANALYSIS_REPOSITORY_ROOT", CODE_ROOT)).resolve()
sys.path.insert(0, str(CODE_ROOT))

from src.ai_analysis import strict_json_loads  # noqa: E402
from src.analysis_readback import publish_split_readback, verify_split_readback  # noqa: E402
from src.analysis_request import validate_analysis_write_request  # noqa: E402
from src.analysis_runtime import persist_assessment, publish_blind_projection, verify_readback  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("publish-blind", "persist-assessment"))
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    args = parser.parse_args()
    snapshot = (ROOT / args.snapshot).resolve()
    if ROOT not in snapshot.parents:
        raise SystemExit("snapshot escapes repository")
    if args.operation == "publish-blind":
        result = publish_blind_projection(ROOT, snapshot)
        verify_readback(ROOT, result)
    else:
        if not args.request:
            raise SystemExit("--request is required")
        request = strict_json_loads(args.request.read_bytes())
        validate_analysis_write_request(ROOT, snapshot, request)
        result = persist_assessment(ROOT, snapshot, request)
        verify_readback(ROOT, result)
        result.update(publish_split_readback(ROOT, result))
        verify_split_readback(ROOT, result)
    public_keys = (
        "path",
        "sha256",
        "readback_manifest_path",
        "readback_manifest_sha256",
        "readback_manifest_canonical_sha256",
        "readback_manifest_byte_length",
        "readback_part_count",
    )
    print(json.dumps({key: result[key] for key in public_keys if key in result}))


if __name__ == "__main__":
    main()
