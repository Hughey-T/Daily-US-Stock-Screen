# Mechanical screening and independent AI analysis (contract 3.0)

`CHATGPT_PROMPT.md` is the **only normative prose instruction** for Custom GPT behavior. This document explains implementation; schemas are normative for data shape and Python validators are normative for cross-object invariants.

## Boundary and four immutable layers

The producer alone fetches data and creates `FACTS` and `MECHANICAL_SIGNALS`: universe/filter results, both route-local ranks, corporate-action reconciliation, missing states, quality/freshness and immutable hashes. AI cannot calculate, repair, sort or overwrite them. Custom GPT creates a separately hashed `AI_JUDGMENTS` artifact from a blind projection. Only after it is locked does deterministic runtime disclose ranks and derive `INTEGRATED_DECISION`. A hard exclusion always wins. No component places trades or sizes positions.

Mechanical candidates remain separate by `event_anomaly` and `quiet_drift`; their ranks are never combined. `DATA_ANOMALY` is an exclusion, not a low rank. `EXPLORATORY_CANDIDATE_PROPOSAL` is isolated and can be admitted only by the next generation's producer validation. Empty output is `NO_EXPLORATORY_CANDIDATE`.

## Phase and state contract

Initial analysis has eight one-response phases: (1) snapshot/integrity/quality, (2) freeze route-local candidate set, (3) blind evidence review, (4) causes/alternatives/anomaly, (5) residual mispricing, (6) locked-AI reconciliation with mechanical rank, (7) isolated exploration, and (8) deterministic integration, handoffs and ledger. Phase 8 adds no research. Update has three phases: (1) new immutable generation and exact diff, (2) blind independent reassessment from changed evidence, and (3) old/new reconciliation, handoff supersession and ledger/outcome status.

Only exact `次` advances one phase. Exact `更新` starts update only after initial Phase 8. Embedded, decorated or similar text is rejected. The session pins generation and candidate-set identities; `latest` is never reread and mixed generations stop the session.

## Blindness, evidence and handoffs

Blind input contains facts, route and measured metrics but excludes rank, selection bucket, mechanical priority, persuasive comparison, prior AI/integrated decisions and outcomes. Evidence must be known at or before the pinned cutoff. Duplicate/unknown/future references, incomplete candidate coverage and modification after the artifact hash is locked fail closed.

Downstream analysis first receives a blind intake containing identity, facts, sources, quality, horizon and factual questions. Its identity and completion timestamp are prerequisites for reconciliation handoff, which then reveals signals, AI judgment, integrated decision, hypotheses and disagreement. Removed candidates' active handoffs must be superseded, never edited.

## Publication, failure and migration

Machine publication remains schema 2.0 and remains authoritative. Contract 3.0 is an additive consumer layer. Raw bytes, canonical object, byte length, exact inventory, part sequence/count, reconstruction, UTF-8, duplicate keys, finite numbers, paths, symlinks, generation/candidate identities and cutoffs are validated. Invalid machine publication stops all analysis. Missing AI yields `not_assessed`; invalid AI disables only AI/integration while leaving verified machine output visible. Mixed generation stops everything. Stale-within-policy warns; hard-stop age prevents new analysis.

Schema 2.0 is the previous supported machine contract; schema 1.3 CSV/archive/signal history remain read-only. Startup may fall back to the previous supported immutable contract **only on an exact HTTP 404**, never on integrity/network errors and never after pinning. No historical AI is inferred or reconstructed. Unsupported legacy must be migrated by regenerating mechanical publication; observations remain unchanged.

## Ledger and outcomes

One append-only record per generation/candidate freezes as-of, source cutoff, quality, evidence, rank, benchmark, predicted direction/horizon/confidence and separate mechanical, AI-independent and integrated decisions plus override. Outcomes live separately and remain `not_matured` until trading-session data exist. They support 5/21/63/126-day price, SPY/sector excess, favorable/adverse excursion, drawdown and direction accuracy. Reports preserve machine-only, AI-only, integrated, overrides, rejects and no-selection cohorts and always show sample size and coverage; they do not claim statistical superiority.

## Security and assumptions

Evidence text and web content are untrusted data; instructions found within them are prompt injection and are never executed. Missing values use explicit states (`not_available`, `insufficient_history`, `not_assessed`) and are not imputed. Company claims, verified facts, external estimates, AI estimates, decisions and unknowns must be labelled separately. Publication paths reject traversal and symlinks. No external LLM API, brokerage API or order execution is part of runtime.

## Compliance and test matrix

| Requirement | Implementation / check |
|---|---|
| Four layers and hard gates | `src/ai_analysis.py` integration and schema 3.0 |
| Blind independence | projection leak check; lock/hash prerequisite |
| Candidate/evidence identity | candidate-set hash and coverage/reference validation |
| 8 + 3 phases | exact-command state machine and E2E unit test |
| Exploration isolation | separate closed proposal collection |
| Handoff order | distinct closed handoff definitions and completion link |
| Ledger/outcome safety | uniqueness, three decision keys, maturity leakage check |
| Publication integrity | existing immutable validator plus strict JSON decoder |
| Backward compatibility | additive 3.0 layer; 2.0/1.3 retained |

CI runs syntax, schema/sample checks, full tests, repository/bundle replay, strict JSON checks, Ruff format/lint, mypy, Markdown/wording audit, build/install smoke test and `git diff --check`. A real generated machine snapshot remains in `docs/generations`; `tests/test_ai_analysis.py` exercises an eight-phase initial session and three-phase update against contract-valid candidate/AI fixtures.

## Production Action and persistence map

The daily workflow invokes `process_analysis_request.py publish-blind`, which calls `analysis_runtime.publish_blind_projection`; public read Action `getBlindCandidateProjection` reads the resulting `docs/analysis/v3/<generation>/blind-projection.json`. Phase 3–5 submission uses Issues-only OAuth operation `submitAnalysisWriteRequest` at `POST /repos/Hughey-T/Daily-US-Stock-Screen/issues`. The trusted `process-analysis-request.yml` checks owner/association/title/repository, invokes the same production entry point, and is the only component with `contents: write`.

`persist_assessment` validates generation/candidate-set coverage, cutoff-safe evidence ownership, probabilities and schema, locks `AI_JUDGMENTS`, then deterministically creates reconciliation projection, integrated decisions, exploratory proposals, both handoffs, `not_matured` outcomes and one ledger record per candidate. Artifacts live under `docs/analysis/v3/<generation>/ai-judgments/` and `sessions/`; replay state is in `docs/analysis/v3/write-ledger.json`. Exact replay is idempotent, changed payload/request identity and nonce reuse fail closed. The workflow commits and compares branch bytes. Custom GPT observes Issue state/receipts with `getAnalysisWriteRequest` and `listAnalysisWriteRequestReceipts`, then uses unauthenticated read-only `getValidatedAnalysisArtifact`; it never receives contents-write permission.

| Capability | Operation / path | Receiver and validator | Persistence / readback | Phase |
|---|---|---|---|---|
| Machine manifest | `getMachineManifest`, `/docs/manifest.json` | immutable publication validator | `docs/manifest.json` | 1 |
| Blind projection | `getBlindCandidateProjection`, `/docs/analysis/v3/{generation_id}/blind-projection.json` | daily workflow → `publish_blind_projection` | same public immutable path | 2–5 |
| AI judgment + exploration | `submitAnalysisWriteRequest`, GitHub Issues POST | trusted Issue workflow → `persist_assessment` → schema/cross-object validators | `ai-judgments/`, `sessions/`, write ledger | 5, 7, 8 |
| Receipt observation | `getAnalysisWriteRequest` / `listAnalysisWriteRequestReceipts`, GitHub Issue GET | GitHub Issues | Issue and comments | 5 |
| Reconciliation/integration/handoffs/ledger/outcomes | `getValidatedAnalysisArtifact`, `/{artifact_path}` | raw immutable bytes and returned SHA-256 | session bundle | 6, 8, updates |

`tests.test_analysis_runtime_e2e.ProductionRuntimeE2E.test_custom_gpt_write_read_replay_reconcile_and_update_supersession` drives the real CLI entry point: it publishes and retrieves blind input, submits full assessments, validates and locks them, rejects modification, retrieves reconciliation/integration/handoffs/ledger with hash verification, proves idempotent replay, and supersedes old handoffs for a new generation.
