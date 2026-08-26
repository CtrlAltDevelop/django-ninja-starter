# Apple sign-in

Authorization-code sign-in against Apple. The most particular of the four.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/oauth/apple/callback` | None | Callback |
| `GET` | `/api/v1/oauth/apple/start` | None | Start |
<!-- /generated:routes -->

## Models

<!-- generated:models -->
#### `AppleAccount`

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
| `is_private_email` | Boolean |  |
| `real_user_status` | PositiveSmallInteger | nullable |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `AppleAccount` | Yes | — | `subject`, `user`, `email`, `email_verified`, `is_private_email`, `last_login_at` |
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `APPLE_OAUTH_CLIENT_ID` | **Yes** | the Services ID registered with Apple. |
| `APPLE_OAUTH_TEAM_ID` | **Yes** | the Apple developer team that owns the key. |
| `APPLE_OAUTH_KEY_ID` | **Yes** | identifies which private key signs the client secret. |
| `APPLE_OAUTH_PRIVATE_KEY` | **Yes** | signs the short-lived client secret Apple requires. |
| `APPLE_OAUTH_REDIRECT_URI` | Recommended | the exact callback URL registered with the provider. |
| `APPLE_OAUTH_SCOPES` | **Yes** | what this application asks the provider for. |
<!-- /generated:settings -->

```bash
DJANGO_OAUTH_PROVIDERS=apple
APPLE_OAUTH_CLIENT_ID=com.example.service      # the Services ID, not the bundle ID
APPLE_OAUTH_TEAM_ID=ABCDE12345
APPLE_OAUTH_KEY_ID=FGHIJ67890
APPLE_OAUTH_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n..."
APPLE_OAUTH_REDIRECT_URI=https://api.example/api/v1/oauth/apple/callback
APPLE_OAUTH_SCOPES="name email"
```

The redirect URI **must** be HTTPS -- Apple posts the callback cross-site and will
not do it over plain HTTP. The system checks refuse a scheme that is not `https`.

There is no static client secret. Apple wants a short-lived JWT signed with your
`.p8` key, which is minted per request from the team ID, key ID and private key.

## Usage

Send the browser to `/api/v1/oauth/apple/start`. On success the callback
redirects to `next_url` and the account is signed in with whatever credential the
active [token mode](../credentials.md) issues. On failure it answers `400` with a
`detail` explaining what went wrong, and the reason is also recorded on the
`SocialLoginAttempt` row.

Link a provider to an account that already exists by calling `/start` while
authenticated.

## Notes

Apple returns the callback as a cross-site `form_post`, not a redirect, so
the attempt-binding cookie is issued with `SameSite=None; Secure`. That is why
HTTPS is not negotiable here.

PKCE is not used: Apple does not support it for the web flow. The nonce in the ID
token and the browser binding carry that weight instead.

A user may choose to hide their real address, in which case Apple supplies a
private relay address. `is_private_email` records that. Mail to a relay address
works, but only from a domain you have registered with Apple.

Apple sends the user's name exactly once, on first authorization, and never
again. If you need it, capture it then.
