# Sliding tokens

One token that extends itself while it is being used, inside an absolute bound.
The simplest of the three modes: no refresh token, no pair to keep in sync.

Active when `DJANGO_AUTH_TOKEN_MODE=sliding`.

## Routes

<!-- generated:routes -->
_This app publishes no routes of its own._
<!-- /generated:routes -->

## Models

<!-- generated:models -->
#### `SlidingToken`

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
| `idle_timeout_seconds` | PositiveInteger |  |
| `absolute_expires_at` | DateTime |  |
| `previous_expires_at` | DateTime | nullable |
| `slide_count` | PositiveInteger |  |

#### `SlidingTokenEvent`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | BigAuto | primary key |
| `token` | ForeignKey | → `oauth_sliding.SlidingToken` |
| `event_type` | Char |  |
| `old_expires_at` | DateTime | nullable |
| `new_expires_at` | DateTime | nullable |
| `ip_address` | GenericIPAddress | nullable |
| `created_at` | DateTime | not editable |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `SlidingToken` | No — revocable only | `revoke_selected` | `id`, `user`, `issued_at`, `expires_at`, `absolute_expires_at`, `slide_count`, `revoked_at` |
| `SlidingTokenEvent` | No — read-only | — | `created_at`, `event_type`, `token`, `old_expires_at`, `new_expires_at` |
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_AUTH_SLIDING_IDLE_TIMEOUT_SECONDS` | Optional | how long a token survives without being used. Range 60–86400. |
| `DJANGO_AUTH_REFRESH_TOKEN_TTL_SECONDS` | Optional | the absolute lifetime no amount of sliding can exceed. Range 300–31536000. |
<!-- /generated:settings -->

```bash
DJANGO_OAUTH_MODE=sliding
DJANGO_AUTH_TOKEN_MODE=sliding
DJANGO_AUTH_SLIDING_IDLE_TIMEOUT_SECONDS=900
DJANGO_AUTH_REFRESH_TOKEN_TTL_SECONDS=1209600
```

Two deadlines, and both matter. The idle timeout is how long the token survives
without being used; the refresh TTL is the absolute ceiling that no amount of
sliding can pass. A session that is genuinely in use lasts up to the ceiling; one
that is abandoned dies within the idle window.

## Usage

A login returns a single token and no refresh token:

```json
{
  "token_type": "bearer",
  "access_token": "eyJhbGciOi...",
  "refresh_token": "",
  "expires_in": 900,
  "session_id": "8c1f..."
}
```

Every authenticated request slides the deadline forward as a side effect, so a
client that is doing anything at all never has to think about renewal. `/refresh`
exists for the case where it needs to ask explicitly -- an app returning from the
background that wants to know whether it still has a session before letting the
user start typing:

```bash
curl -X POST .../auth/token/refresh -H 'Authorization: Bearer eyJhbGciOi...'
```

The token that comes back is the same value. Only the deadline moved.

```bash
curl .../auth/token/sessions -H 'Authorization: Bearer eyJhbGciOi...'
curl -X DELETE .../auth/token/sessions/8c1f... -H 'Authorization: Bearer eyJhbGciOi...'
```

## Notes

The signature covers the **absolute** lifetime, not the idle one. Stamping the
idle expiry into the JWT would have let the signature go stale while the session
it stood for was still very much alive; the idle timeout is enforced by the row
instead. This is the one place where the two halves of a credential deliberately
disagree about when it ends.

Every extension is recorded in `SlidingTokenEvent`, along with issue and
revocation, so "when was this session last actually used" has an answer that
survives the row being cleaned up.

Choose this mode when you want sessions that quietly outlast a working day
without a refresh dance. Choose [rotation](rotation.md) instead if you need theft
detection, or [session](session.md) if you need to revoke one device without
touching the others.
