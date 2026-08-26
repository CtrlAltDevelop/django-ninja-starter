# Server-side sessions

A long-lived session key that mints short, independently revocable access tokens.
The mode to pick when an account needs a device list it can act on.

Active when `DJANGO_AUTH_TOKEN_MODE=session`.

## Routes

<!-- generated:routes -->
_This app publishes no routes of its own._
<!-- /generated:routes -->

## Models

<!-- generated:models -->
#### `OAuthSession`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `user` | ForeignKey | → `auth.User` |
| `client` | ForeignKey | → `oauth_core.OAuthClient`, nullable |
| `session_key_hash` | Char | unique, not editable |
| `device_id` | Char |  |
| `scopes` | JSON |  |
| `audience` | Char |  |
| `created_at` | DateTime | not editable |
| `last_seen_at` | DateTime |  |
| `expires_at` | DateTime |  |
| `revoked_at` | DateTime | nullable |
| `revocation_reason` | Char |  |
| `ip_address` | GenericIPAddress | nullable |
| `user_agent` | Text |  |
| `metadata` | JSON |  |

#### `SessionAccessToken`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `user` | ForeignKey | → `auth.User` |
| `client` | ForeignKey | → `oauth_core.OAuthClient`, nullable |
| `token_hash` | Char | unique, not editable |
| `scopes` | JSON |  |
| `audience` | Char |  |
| `issued_at` | DateTime | not editable |
| `expires_at` | DateTime |  |
| `last_used_at` | DateTime | nullable |
| `revoked_at` | DateTime | nullable |
| `revocation_reason` | Char |  |
| `issued_ip` | GenericIPAddress | nullable |
| `user_agent` | Text |  |
| `metadata` | JSON |  |
| `session` | ForeignKey | → `oauth_session.OAuthSession` |

#### `SessionRevocation`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | BigAuto | primary key |
| `session` | ForeignKey | → `oauth_session.OAuthSession` |
| `revoked_by` | ForeignKey | → `auth.User`, nullable |
| `reason` | Char |  |
| `access_tokens_revoked` | PositiveInteger |  |
| `ip_address` | GenericIPAddress | nullable |
| `created_at` | DateTime | not editable |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `OAuthSession` | No — revocable only | `revoke_selected` | `id`, `user`, `created_at`, `last_seen_at`, `expires_at`, `revoked_at` |
| `SessionAccessToken` | No — revocable only | `revoke_selected` | `id`, `user`, `session`, `issued_at`, `expires_at`, `revoked_at` |
| `SessionRevocation` | No — read-only | — | `created_at`, `session`, `reason`, `access_tokens_revoked`, `revoked_by` |
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_AUTH_ACCESS_TOKEN_TTL_SECONDS` | Optional | how long each access token minted from the session lasts. Range 60–86400. |
| `DJANGO_AUTH_REFRESH_TOKEN_TTL_SECONDS` | Optional | how long the session itself lasts. Range 300–31536000. |
<!-- /generated:settings -->

```bash
DJANGO_OAUTH_MODE=session
DJANGO_AUTH_TOKEN_MODE=session
DJANGO_AUTH_ACCESS_TOKEN_TTL_SECONDS=3600
DJANGO_AUTH_REFRESH_TOKEN_TTL_SECONDS=1209600
```

## Usage

A login returns both halves:

```json
{
  "token_type": "bearer",
  "access_token": "eyJhbGciOi...",
  "refresh_token": "eyJhbGciOi...",
  "expires_in": 3600,
  "session_id": "8c1f..."
}
```

The access token goes in the `Authorization` header. The session key is presented
only at `/refresh`, and comes straight back unchanged:

```bash
curl -X POST .../auth/token/refresh -d '{"refresh_token": "eyJhbGciOi..."}'
```

That is deliberate, and it is the difference from [rotation](rotation.md): the
session key is stable, so a client can hold one and mint many access tokens
against it.

### Listing and ending devices

```bash
curl .../auth/token/sessions -H 'Authorization: Bearer ...'
```

```json
{
  "mode": "session",
  "sessions": [
    {
      "session_id": "8c1f...",
      "created_at": "2026-08-20T09:14:02Z",
      "last_used_at": "2026-08-25T11:02:44Z",
      "expires_at": "2026-09-03T09:14:02Z",
      "ip_address": "203.0.113.7",
      "user_agent": "Mozilla/5.0 ...",
      "auth_method": "password"
    }
  ]
}
```

```bash
curl -X DELETE .../auth/token/sessions/8c1f... -H 'Authorization: Bearer ...'
```

Ending a session retires every access token minted under it, and records how many
that was in `SessionRevocation`. Deletion is scoped to the caller, so one account
cannot end another's session.

## Notes

Access tokens are independent of each other and of the session key. Two access
tokens from the same session both keep working until the session ends or they
individually expire -- which is exactly what makes "sign out my other devices"
implementable, and what distinguishes this mode from the other two.

`last_seen_at` is updated whenever an access token is presented or the session is
refreshed, so the device list is honest about which entry is stale.

Choose this mode for a product with a visible session manager. Choose
[rotation](rotation.md) if you would rather have theft detection than a device
list, or [sliding](sliding.md) if you want neither.
