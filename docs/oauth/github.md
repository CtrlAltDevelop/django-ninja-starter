# GitHub sign-in

Authorization-code sign-in against GitHub. Not OpenID Connect, so it works a little differently from the other three.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/oauth/github/callback` | None | Callback |
| `GET` | `/api/v1/oauth/github/start` | None | Start |
<!-- /generated:routes -->

## Models

<!-- generated:models -->
#### `GitHubAccount`

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
| `login` | Char |  |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `GitHubAccount` | Yes | — | `subject`, `user`, `email`, `email_verified`, `login`, `last_login_at` |
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `GITHUB_OAUTH_CLIENT_ID` | **Yes** | identifies this application to GitHub. |
| `GITHUB_OAUTH_CLIENT_SECRET` | **Yes** | proves the token request came from this application. |
| `GITHUB_OAUTH_REDIRECT_URI` | Recommended | the exact callback URL registered with the provider. |
| `GITHUB_OAUTH_SCOPES` | **Yes** | what this application asks the provider for. |
<!-- /generated:settings -->

```bash
DJANGO_OAUTH_PROVIDERS=github
GITHUB_OAUTH_CLIENT_ID=<from the OAuth app settings>
GITHUB_OAUTH_CLIENT_SECRET=<from the OAuth app settings>
GITHUB_OAUTH_REDIRECT_URI=https://api.example/api/v1/oauth/github/callback
GITHUB_OAUTH_SCOPES="read:user user:email"
```

`user:email` is worth keeping. Without it the profile response carries no address
at all when the user has theirs set to private.

## Usage

Send the browser to `/api/v1/oauth/github/start`. On success the callback
redirects to `next_url` and the account is signed in with whatever credential the
active [token mode](../credentials.md) issues. On failure it answers `400` with a
`detail` explaining what went wrong, and the reason is also recorded on the
`SocialLoginAttempt` row.

Link a provider to an account that already exists by calling `/start` while
authenticated.

## Notes

GitHub issues no ID token, so the profile is fetched from the REST API after
the exchange and the account's primary verified address is read from a second
call. There is nothing signed to verify, which is why this provider relies
entirely on `state`, the browser binding, and PKCE.

`login` on `GitHubAccount` records the GitHub username. Do not key anything on
it: a user can change it, and somebody else can then claim it. `subject` -- the
numeric account ID -- is the only stable identifier.
