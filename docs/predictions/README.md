# Prediction records

Prediction CSV files in this directory use `prediction_schema_version=1.0`.
They are append-only: after a CSV has reached the base branch it must never be
edited, renamed, or deleted. Future outcomes belong in `docs/verifications/`.

IDs are derived from UTF-8 strings with the separators shown below. Tickers are
trimmed and uppercased; all other inputs must already be in their canonical CSV
form.

```text
candidate_id = "cand_" + sha256(
  market_data_date + "|" + source_dataset + "|" + upper(ticker) + "|" + config_hash
)

prediction_id = "pred_" + sha256(
  candidate_id + "|" + decimal verification_horizon
)
```

`source_snapshot` points to a local repository manifest such as
`docs/snapshots/2026-07-13/snapshot.json`, or to a retrievable HTTPS manifest.
The manifest identifies the exact `latest.json`, `latest.csv`, and
`quiet_drift.csv` inputs and records their SHA-256 hashes.

Run these commands before committing a prediction:

```bash
python scripts/validate_prediction_record.py
python scripts/rebuild_prediction_index.py
```

For deterministic rebuilds, each index entry normalizes `created_at` to
`run_dateT00:00:00Z`. `earliest_verification_dates` advances
`market_data_date` by the requested number of XNYS trading sessions.
