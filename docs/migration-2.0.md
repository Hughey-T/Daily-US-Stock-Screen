# Migration from screening 1.3 / prediction 1.1 to 2.0

Historical files are immutable and remain readable by `scripts/validate_prediction_record.py`. Do not backfill them from conversation or overwrite them. New runs write schema 2.0 immutable snapshots and JSON prediction/verification bundles validated by `scripts/validate_v2_records.py`.

A legacy `candidate_id(date, route, ticker, config)` maps deterministically to `entity_id(date,ticker,config)` plus `route_candidate_id(entity,route)` at read time; no old file is edited. Legacy index entries remain valid historical entries. New IDs also include run, prompt and entry timestamp, making identical reruns idempotent while any material assessment version produces a new immutable ID.

During migration `latest.json`, `latest.csv`, and `quiet_drift.csv` stay published. New clients use `manifest.json`; legacy clients may keep reading mutable files, but a single analysis may not combine those modes. Unsupported versions stop with an explicit validation error.
