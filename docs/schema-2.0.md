# Schema 2.0 data dictionary and operations

`manifest.json` is a mutable pointer fetched once. Its immutable target is `generations/<market-date>/<generation-id>/snapshot.json`. `snapshot.json` records snapshot/generation IDs, cutoff, config/hash, code version, event/quiet/Phase 2/Phase 3 paths and hashes, row counts, corporate-action validator, price and benchmark definitions. Mutable compatibility files cannot be used as a cross-generation fallback.

Prediction bundles separate `candidate_assessments` from `horizon_forecasts`. Assessments contain entity/route IDs, record group, orthogonal research/stance/situation/priority/applicability concepts, valuation ranges/method/limitations, deterministic display class and thesis. Forecasts contain unique versioned IDs, horizon/directions, frozen neutral thresholds, forecast confidence/probability, next-tradable entry, and benchmark quality. Industry benchmark, then peer basket, sector ETF, SPY, unavailable is the preference order; limitations cap confidence.

Research stores latest-any SEC separately from latest-material SEC date/type. Common-gate results and an applicable industry module use `fact_found`, `searched_none`, `not_applicable`, `unavailable`, or `unverified`; unknown industry cannot be complete.

Verification is append-only and separate. Price return excludes distributions; total shareholder return includes them. Intraday adjusted highs/lows (from and including the post-entry portion of entry day) define direction-oriented MFE/MAE. Lifecycle outcomes are not missing data. Forecast, comparison-only, and monitor-only records have directional, selection, and resolution evaluation groups respectively.

Local persistence progresses through generated → schema_validated →
repository_written → indexed_local (or failed). A separate GitHub-side proof may
report integrity_verified only after remote branch/commit and index checks. Only
that remotely proven state may be described as saved.

Generation identity hashes `latest.json`, event CSV, quiet CSV, normalized config,
and code identity. Consequently a forced rerun whose generated timestamp or
quality metadata changes creates a new generation even when CSV rows do not;
only identical authoritative bytes are idempotent. Phase 2/3 hashes are included
in the snapshot identity. Each artifact has its own Draft 2020-12 schema and is
semantically re-audited whenever the snapshot is opened.

The scheduled `Resolve next-session entry prices` workflow reads pending rows by
generation, downloads the actual Yahoo open after `first_tradable_at`, and writes
a new immutable `docs/entry-resolutions/<generation>/<resolution>.json`. Pending,
resolved and unavailable are explicit. Predictions pin its path and hash.

Prediction production requires a Phase 4/5 research object containing verified
facts, company claims, inferences, assessment fields, valuation and explicit
forecast directions/probabilities. Missing or partial research is never replaced
with a synthetic neutral forecast. Scheduled verification calculates due
horizons, price and total returns, benchmark relatives, MFE/MAE and lifecycle
statuses into separately indexed immutable bundles.

Local persistence ends at `indexed_local`. GitHub `integrity_verified` requires
the repository, branch and commit SHA plus remote file/index re-fetch and exact
hash/schema/snapshot/count/timestamp checks. The Custom GPT has no authenticated
writer in this repository, so its Phase 6 persistence remains blocked rather
than being described as saved.

Troubleshooting: a hash or row mismatch stops the run; action-provider failure degrades and quarantines affected tickers; comparison shortage is `not_evaluable`; unavailable industry benchmark uses a lower-quality fallback without changing route scores; SEC/IR failure marks research incomplete; a not-yet-due horizon is `not_yet_due`, not an empty outcome.
