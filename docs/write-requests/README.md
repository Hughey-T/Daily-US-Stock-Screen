# Write-request queue

`ledger.json` is an append-only logical ledger: existing request and nonce entries are never changed or removed. Receipts progress from `pending_remote_verification` to `integrity_verified` only after commit- and branch-addressed bytes agree. Workflow concurrency serializes ledger/index updates. Replays with the same request and payload return the existing receipt; nonce reuse or payload conflicts fail closed.
