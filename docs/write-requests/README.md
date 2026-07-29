# Write-request queue and recovery

The ledger has append-only semantics: request IDs and nonces are never changed, removed, or reused. Each entry also pins the originating Issue number. A new write moves `not_written → pending_remote_verification` only after prediction, index, ledger, and the pending receipt are pushed. The pending state means **repository bytes written; remote integrity pending**, not “nothing saved.”

A replay with the same request and payload is classified as `resume_pending` or `verified_replay`; it never regenerates a prediction or duplicates an index/ledger entry. Resume audits the prediction hash/schema, index entry, snapshot, entry resolution, counts, timestamp, receipt, and ledger before remote proof. Conflicting payload/nonce or a `failed_terminal` receipt stops.

`recover-gpt-write-requests.yml` runs twice hourly and can also be dispatched. It shares the writer concurrency group, retries pending remote proof, completes the original Issue, and revisits final receipts whose notification was interrupted. Network timeout and HTTP 404/429/502/503/504 are retried with exponential backoff. Hash/schema/identity/index conflicts are terminal and are not retried.

Final receipts record the Issue, first-written and verified commits, prediction/index hashes, normalized self-hash, retry count, last attempt, and completion time. A receipt remains pending until commit- and branch-addressed prediction/index bytes and final receipt bytes agree.
