#!/usr/bin/env python3
"""Validate and append a schema 2.0 prediction or verification bundle locally.

This command intentionally cannot assert GitHub persistence; prediction receipts
stop at ``indexed_local`` until a repository-side confirmation process verifies the
committed/pushed index.
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.validate_v2_records import validate_prediction_data,validate_verification_data
from src.records import persist_prediction_bundle,persist_verification_bundle

def main():
 p=argparse.ArgumentParser();p.add_argument('kind',choices=['prediction','verification']);p.add_argument('input');args=p.parse_args();data=json.loads(Path(args.input).read_text())
 if args.kind=='prediction':
  path,receipt=persist_prediction_bundle(data,ROOT,validate_prediction_data);print(f"wrote {path.relative_to(ROOT)}; persistence_state={receipt['state']}")
 else:
  path=persist_verification_bundle(data,ROOT,validate_verification_data);print(f"wrote {path.relative_to(ROOT)}; persistence_state=repository_written")
if __name__=='__main__':main()
