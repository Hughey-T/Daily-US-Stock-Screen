# Verification records

Verification CSV files are stored separately from predictions and join to them
with `prediction_id`. Never append future returns to a prediction CSV.

All stock, SPY-relative, and sector-relative returns use split-adjusted closing
prices and exclude dividends, matching the screening pipeline. One verification
row is allowed for each `prediction_id`; its horizon must match the prediction.

Direction-hit fields and directional `outcome` are populated only for
`prediction_applicability=forecast`. For `comparison_only` and `monitor_only`,
they remain empty. Comparison records still retain stock, SPY-relative, and
sector-relative returns together with favorable and adverse excursions.
# Schema 2.0

Schema 2.0 verification bundles use
`schemas/v2.0/verification_bundle.schema.json` and are append-only files under
`docs/verifications/v2/`. They retain the immutable prediction path and SHA-256;
validation re-hashes the prediction and checks forecast/comparison/monitor
evaluation groups. A verification never edits its source prediction.
`Generate prediction verifications` runs automatically, records source-data
cutoff/hash, emits explicit `not_yet_due` or `data_unavailable`, and creates a
new version for each market-data hash rather than overwriting an earlier result.
