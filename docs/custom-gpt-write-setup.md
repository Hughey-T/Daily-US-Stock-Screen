# One-time Custom GPT write setup

## GitHub App

1. As **Hughey-T**, open GitHub **Settings → Developer settings → GitHub Apps → New GitHub App**.
2. Use the Custom GPT editor's displayed OAuth callback URL verbatim as **User authorization callback URL**. Enable expiring user-to-server tokens. A webhook is unnecessary; disable **Active**.
3. Repository permissions: **Issues: Read and write**; **Metadata: Read-only** is implicit. Set Contents, Actions, Workflows, Pull requests, Administration, and every other permission to **No access**.
4. Install with **Only select repositories**, selecting only `Daily-US-Stock-Screen`.
5. Never store the Client ID/Client Secret in this repository. Do not substitute a Personal Access Token or install the App across all repositories.

## Custom GPT Action

1. Import `openapi/gpt-write-action.yaml` in the private GPT's Action editor.
2. Select OAuth. Authorization URL: `https://github.com/login/oauth/authorize`; token URL: `https://github.com/login/oauth/access_token`; Client ID/Secret: the App values; scope: empty; token exchange: the editor's standard setting.
3. Save the callback URL shown by the editor in the GitHub App, authorize, and test the connection.
4. In Preview, use an intentionally expired/test envelope to confirm a harmless validation failure; do not submit a valid production request as a dry run.
5. Verify the imported Action exposes exactly submit, get Issue, and list comments. Keep the GPT private until the privacy notice is linked and reviewed.

Never paste client secrets, access/refresh tokens, or cookies into an Issue, prompt, repository file, or log. Normal users continue with only `更新`, `次`, and `検証`.
