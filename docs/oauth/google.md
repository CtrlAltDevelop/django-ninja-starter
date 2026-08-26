# Google sign-in

Authorization-code sign-in against Google, with PKCE and an ID token.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/oauth/google/callback` | None | Callback |
| `GET` | `/api/v1/oauth/google/start` | None | Start |
<!-- /generated:routes -->

## Models

<!-- generated:models -->
#### `GoogleAccount`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `user` | ForeignKey | → `accounts.User` |
| `subject` | Char | unique |
| `email` | Char |  |
| `email_verified` | Boolean |  |
| `display_name` | Char |  |
| `avatar_url` | Char |  |
| `scopes` | JSON |  |
| `access_token_encrypted` | Text | not editable |
| `refresh_token_encrypted` | Text | not editable |
| `token_expires_at` | DateTime | nullable |
| `raw_claims` | JSON |  |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `last_login_at` | DateTime | nullable |
| `hosted_domain` | Char |  |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `GoogleAccount` | Yes | — | `subject`, `user`, `email`, `email_verified`, `hosted_domain`, `last_login_at` |
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `GOOGLE_OAUTH_CLIENT_ID` | **Yes** | identifies this application to Google. |
| `GOOGLE_OAUTH_CLIENT_SECRET` | **Yes** | proves the token request came from this application. |
| `GOOGLE_OAUTH_REDIRECT_URI` | Recommended | the exact callback URL registered with the provider. |
| `GOOGLE_OAUTH_SCOPES` | **Yes** | what this application asks the provider for. |
<!-- /generated:settings -->

```bash
DJANGO_OAUTH_PROVIDERS=google
GOOGLE_OAUTH_CLIENT_ID=<from the Google Cloud console>
GOOGLE_OAUTH_CLIENT_SECRET=<from the Google Cloud console>
GOOGLE_OAUTH_REDIRECT_URI=https://api.example/api/v1/oauth/google/callback
GOOGLE_OAUTH_SCOPES="openid email profile"
```

Register the redirect URI in the Google Cloud console exactly as written above --
Google matches it character for character.

## Usage

Send the browser to `/api/v1/oauth/google/start`. On success the callback
redirects to `next_url` and the account is signed in with whatever credential the
active [token mode](../credentials.md) issues. On failure it answers `400` with a
`detail` explaining what went wrong, and the reason is also recorded on the
`SocialLoginAttempt` row.

Link a provider to an account that already exists by calling `/start` while
authenticated.

## Notes

The profile comes from the ID token rather than a second request to a
userinfo endpoint, so a sign-in is one round trip after the exchange.

`access_type=offline` and `include_granted_scopes=true` are sent, so a refresh
token arrives on first consent and previously granted scopes are carried forward.

Google publishes two issuer strings for the same tokens
(`https://accounts.google.com` and `accounts.google.com`); both are accepted.

`hosted_domain` on `GoogleAccount` records the Workspace domain, if any. Use it if
you need to restrict sign-in to one organisation.
