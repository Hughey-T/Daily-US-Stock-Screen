# Prediction records

Prediction CSV files in this directory use `prediction_schema_version=1.1`.
They are append-only: after a CSV has reached the base branch it must never be
edited, renamed, or deleted. Future outcomes belong in `docs/verifications/`.
Schema 1.1 follows the merge of the initial 1.0 infrastructure and adds explicit
applicability rules before any production prediction CSV has been stored.

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

`run_id` identifies one Custom GPT execution. For a given `candidate_id` and
`run_id`, all `forecast` rows must use one `action_class` from `A` through `E`.
Use optional semicolon-separated `secondary_tags` for auxiliary classifications.

`prediction_applicability` controls which fields are evaluable:

- `forecast` maps to `final_candidate` and requires both predicted directions
  plus one action class.
- `comparison_only` maps to `nonselected_comparison`; predicted directions must
  be empty and action class may be empty.
- `monitor_only` maps to `unresolved_monitor`; predicted directions and future
  direction-hit results are not applicable.

Prediction CSVs must never contain future-return, excursion, direction-hit,
outcome, verification-date, or verification-source columns. Optional
`thesis_fact_date` cannot be later than `market_data_date`; optional
`thesis_source_url` must be HTTP(S).

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
