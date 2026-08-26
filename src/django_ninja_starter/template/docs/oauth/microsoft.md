# Microsoft sign-in

Authorization-code sign-in against Microsoft Entra ID, personal or work accounts depending on the tenant.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/oauth/microsoft/callback` | None | Callback |
| `GET` | `/api/v1/oauth/microsoft/start` | None | Start |
<!-- /generated:routes -->

## Models

<!-- generated:models -->
#### `MicrosoftAccount`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `user` | ForeignKey | → `auth.User` |
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
| `tenant_id` | Char |  |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `MicrosoftAccount` | Yes | — | `subject`, `user`, `email`, `email_verified`, `tenant_id`, `last_login_at` |
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `MICROSOFT_OAUTH_CLIENT_ID` | **Yes** | identifies this application to Microsoft. |
| `MICROSOFT_OAUTH_CLIENT_SECRET` | **Yes** | proves the token request came from this application. |
| `MICROSOFT_OAUTH_TENANT` | **Yes** | which directory may sign in: common, organizations, consumers, or a GUID. |
| `MICROSOFT_OAUTH_REDIRECT_URI` | Recommended | the exact callback URL registered with the provider. |
| `MICROSOFT_OAUTH_SCOPES` | **Yes** | what this application asks the provider for. |
<!-- /generated:settings -->

```bash
DJANGO_OAUTH_PROVIDERS=microsoft
MICROSOFT_OAUTH_CLIENT_ID=<application (client) ID>
MICROSOFT_OAUTH_CLIENT_SECRET=<client secret>
MICROSOFT_OAUTH_TENANT=common
MICROSOFT_OAUTH_REDIRECT_URI=https://api.example/api/v1/oauth/microsoft/callback
MICROSOFT_OAUTH_SCOPES="openid email profile offline_access"
```

The tenant decides who may sign in:

| Value | Who |
| --- | --- |
| `common` | Work, school and personal Microsoft accounts |
| `organizations` | Work and school accounts only |
| `consumers` | Personal Microsoft accounts only |
| A tenant GUID | One directory only |

Anything else is refused by the system checks, because a typo here silently
widens or narrows who can get in.

## Usage

Send the browser to `/api/v1/oauth/microsoft/start`. On success the callback
redirects to `next_url` and the account is signed in with whatever credential the
active [token mode](../credentials.md) issues. On failure it answers `400` with a
`detail` explaining what went wrong, and the reason is also recorded on the
`SocialLoginAttempt` row.

Link a provider to an account that already exists by calling `/start` while
authenticated.

## Notes

The authorization and token endpoints follow whatever tenant is configured
*now*, resolved per request rather than baked in when the module was first
imported. Changing `MICROSOFT_OAUTH_TENANT` therefore takes effect on reload
without a stale endpoint lingering.

`offline_access` is in the default scopes because Microsoft will not issue a
refresh token without it.

`tenant_id` on `MicrosoftAccount` records the directory the user actually came
from, which is what you want when the configured tenant is `common` and you still
need to know.
