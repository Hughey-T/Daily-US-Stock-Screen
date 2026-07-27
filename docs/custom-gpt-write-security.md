# Custom GPT write security model

The OAuth token can only create/read Issues in one installed repository; it has no Contents, Git data, Actions, workflow, ref, PR, or administration permission. A leaked GPT token therefore cannot alter repository bytes. The default-branch `issues: opened` workflow is the separate writer trust domain and has job-scoped `contents: write`/`issues: write`.

The workflow trusts only title/body embedded in the original opened event, plus owner identity and association. It never refetches edited Issue text, checks out an Issue-supplied ref, or interpolates request data into shell, paths, branch names, or commit messages. JSON Schema and semantic checks cover size, timestamps, UUID, hashes, immutable sources, research/cutoff/entry eligibility, replay, prediction schema and index integrity. Work is serialized; pushes are non-force with at most three rebase/retries.

A pending local receipt is not persistence. Success requires identical prediction bytes at commit and `main` URLs, a matching remote index, a second committed final receipt, and matching final receipt bytes. Errors returned to Issues use fixed codes without exceptions, environment values, stack traces, or secrets.

## Crash recovery and visibility

A failure after the first push deliberately leaves an auditable pending receipt rather than deleting append-only bytes. The scheduled recovery workflow resumes from the ledger without regenerating predictions. Pending Issues stay open and say that repository bytes exist but integrity proof is pending; only terminal integrity failures or verified success close the Issue. A final-receipt notification failure is recovered by rechecking already verified remote bytes and the still-open original Issue.
