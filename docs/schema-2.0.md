# Schema 2.0 data dictionary and operations

`manifest.json` is a mutable pointer fetched once. `snapshot.json` is immutable and records snapshot ID, generation/cutoff timestamps, config version/hash, schema, event/quiet paths/hashes/rows, corporate-action validator, price and benchmark definitions. Mutable compatibility files cannot be used as a cross-generation fallback.

Prediction bundles separate `candidate_assessments` from `horizon_forecasts`. Assessments contain entity/route IDs, record group, orthogonal research/stance/situation/priority/applicability concepts, valuation ranges/method/limitations, deterministic display class and thesis. Forecasts contain unique versioned IDs, horizon/directions, frozen neutral thresholds, forecast confidence/probability, next-tradable entry, and benchmark quality. Industry benchmark, then peer basket, sector ETF, SPY, unavailable is the preference order; limitations cap confidence.

Research stores latest-any SEC separately from latest-material SEC date/type. Common-gate results and an applicable industry module use `fact_found`, `searched_none`, `not_applicable`, `unavailable`, or `unverified`; unknown industry cannot be complete.

Verification is append-only and separate. Price return excludes distributions; total shareholder return includes them. Intraday adjusted highs/lows (from and including the post-entry portion of entry day) define direction-oriented MFE/MAE. Lifecycle outcomes are not missing data. Forecast, comparison-only, and monitor-only records have directional, selection, and resolution evaluation groups respectively.

Persistence progresses through generated → schema_validated → repository_written → committed → pushed → indexed → integrity_verified (or failed). Only the last state may be described as saved. Workflows validate schemas/hashes/index before reporting success.

Troubleshooting: a hash or row mismatch stops the run; action-provider failure degrades and quarantines affected tickers; comparison shortage is `not_evaluable`; unavailable industry benchmark uses a lower-quality fallback without changing route scores; SEC/IR failure marks research incomplete; a not-yet-due horizon is `not_yet_due`, not an empty outcome.
