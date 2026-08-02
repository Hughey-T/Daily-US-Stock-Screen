# Privacy Policy for Daily Stock Screen Custom GPT Actions

Effective date: 2026-08-02

This policy describes how the Custom GPT named **Daily Stock Screen** handles information when its Actions connect to the public repository `Hughey-T/Daily-US-Stock-Screen` and to the GitHub API.

## Information processed

### Read-only Action

The read-only Action retrieves public, immutable market-screening and analysis artifacts from `raw.githubusercontent.com`. It does not require authentication and does not intentionally collect personal information.

### GitHub write-request Action

When a user authorizes and invokes the GitHub write-request Action, the Action may process:

- the user's GitHub account identity and authorization information supplied through GitHub OAuth;
- the generated analysis request submitted by the GPT;
- the GitHub Issue number, title, body, state, timestamps, and processing receipts;
- validated analysis artifacts and integrity metadata produced from that request.

The repository is public. Requests submitted as GitHub Issues, Issue comments, and committed analysis artifacts may therefore be publicly visible. Users must not submit passwords, API keys, financial-account credentials, private brokerage information, or other sensitive personal information.

## Purpose of processing

Information is processed only to:

- retrieve the current immutable screening publication;
- submit a tightly scoped analysis request;
- validate, persist, and verify the resulting analysis artifacts;
- return processing status and integrity receipts;
- maintain an auditable and reproducible analysis history.

The Actions do not place trades, connect to a brokerage account, size positions, process payments, or sell personal information.

## Service providers and disclosures

The Actions rely on:

- **OpenAI / ChatGPT**, which operates the Custom GPT interface;
- **GitHub**, which provides repository hosting, OAuth, Issues, Actions, and public file delivery.

Information may be processed by these providers under their own terms and privacy policies. Information is not disclosed to advertisers by the repository owner.

## Retention

GitHub Issues, comments, workflow records, and committed analysis artifacts may be retained indefinitely to preserve the system's append-only audit history. Content may be removed when required for security, legal, or operational reasons, but immutable analysis records are not routinely edited after validation.

## Security

The write workflow is restricted to the fixed repository and validates the repository, request type, request identity, owner authorization, schema, hashes, and replay state before persistence. No system can guarantee absolute security, and users should avoid submitting sensitive information.

## User choices

Users may decline an Action request before it runs. GitHub OAuth connections can be reviewed or revoked through the user's GitHub account and ChatGPT connection settings. Public GitHub content may be viewed without authentication.

## Contact

Questions or removal requests may be submitted through the repository's GitHub Issues page. Do not include sensitive personal information in a public Issue.

## Changes

This policy may be updated when the Actions, data flows, or service providers change. The effective date above indicates the current version.
