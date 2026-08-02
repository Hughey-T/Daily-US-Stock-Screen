# User output design

> `CHATGPT_PROMPT.md` is the sole normative prose instruction. This document is explanatory.

## Design goal

The user should see what is being checked, what is known, why a security remains or is removed, current progress, the next action, and whether persistence verification completed. The presentation must not weaken or replace any internal analysis, screening, persistence or integrity check.

## Two layers

Eight internal initial stages remain unchanged. The conversation combines snapshot validation and candidate freezing into user Phase 1, then presents movement, causes, residual assessment and persistence, reconciliation, exploration, and final integration as Phases 2–7. Updates have three Phases. One response covers at most one user Phase.

Persistence is deliberately visible as a plain-language state rather than as Issues, hashes or artifact paths. Phase 4 cannot advance while confirmation is pending, and retrying checks the existing saved result rather than submitting again. Once confirmed, Phase 5 may compare the locked independent assessment with the mechanical result.

## Result semantics

- **No selection:** processing and persistence succeeded, but no security met final criteria.
- **Insufficient evidence:** processing succeeded, but evidence cannot support a decision.
- **Pending:** analysis exists, but persistence/readback has not completed.
- **Terminal error:** correctness cannot be established, so the result is unusable and incomplete.
- **Partial warning:** only affected securities are unevaluable; valid securities continue under the existing quality rules.

Technical internals are hidden in ordinary replies. When a user explicitly asks to debug them, the response first gives their user-facing meaning and then a separate technical section.

## Examples and verification

Representative initial, pending, final, update and error responses live in `tests/fixtures/user-output/`. `tests/test_user_output_contract.py` checks phase titles/endings, forbidden internals, result-state distinctions and fixture classification consistency. These examples explain the contract; they do not replace the canonical prompt.
