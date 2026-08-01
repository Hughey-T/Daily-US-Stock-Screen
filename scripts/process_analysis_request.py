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
    else:
        if not args.request:
            raise SystemExit("--request is required")
        result = persist_assessment(ROOT, snapshot, json.loads(args.request.read_text()))
    verify_readback(ROOT, result)
    print(json.dumps({key: result[key] for key in ("path", "sha256") if key in result}))


if __name__ == "__main__":
    main()
