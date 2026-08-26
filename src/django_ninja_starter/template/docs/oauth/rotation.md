# Rotating refresh tokens

Single-use refresh tokens with rotation ancestry and reuse detection. The
strictest of the three modes, and the one to reach for when a stolen refresh
token is the threat you actually care about.

Active when `DJANGO_AUTH_TOKEN_MODE=rotation`, and the default when
`DJANGO_OAUTH_MODE=all`.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/token/refresh` | None | Exchange a refresh token for a new pair |
| `POST` | `/api/v1/auth/token/revoke` | None | Revoke the presented credential |
| `GET` | `/api/v1/auth/token/sessions` | Bearer | List this account's live sessions |
| `DELETE` | `/api/v1/auth/token/sessions/{session_id}` | Bearer | End one of this account's sessions |
<!-- /generated:routes -->

## Models

<!-- generated:models -->
#### `RefreshTokenReuseEvent`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | BigAuto | primary key |
| `family` | ForeignKey | → `oauth_rotation.TokenFamily` |
| `refresh_token` | ForeignKey | → `oauth_rotation.RotatingRefreshToken`, nullable |
| `presented_fingerprint` | Char |  |
| `ip_address` | GenericIPAddress | nullable |
| `user_agent` | Text |  |
| `metadata` | JSON |  |
| `detected_at` | DateTime | not editable |

#### `RotatingAccessToken`

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
| `family` | ForeignKey | → `oauth_rotation.TokenFamily` |
| `issued_from` | ForeignKey | → `oauth_rotation.RotatingRefreshToken`, nullable |

#### `RotatingRefreshToken`

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
| `family` | ForeignKey | → `oauth_rotation.TokenFamily` |
| `parent` | ForeignKey | → `oauth_rotation.RotatingRefreshToken`, nullable |
| `replaced_by` | OneToOne | unique, → `oauth_rotation.RotatingRefreshToken`, nullable |
| `rotation_index` | PositiveInteger |  |
| `used_at` | DateTime | nullable |
| `reuse_detected_at` | DateTime | nullable |

#### `TokenFamily`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `user` | ForeignKey | → `auth.User` |
| `client` | ForeignKey | → `oauth_core.OAuthClient`, nullable |
| `scopes` | JSON |  |
| `audience` | Char |  |
| `created_at` | DateTime | not editable |
| `last_rotated_at` | DateTime | nullable |
| `expires_at` | DateTime |  |
| `revoked_at` | DateTime | nullable |
| `revocation_reason` | Char |  |
| `reuse_detected_at` | DateTime | nullable |
| `device_id` | Char |  |
| `issued_ip` | GenericIPAddress | nullable |
| `user_agent` | Text |  |
| `metadata` | JSON |  |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `RefreshTokenReuseEvent` | No — read-only | — | `detected_at`, `family`, `refresh_token`, `presented_fingerprint`, `ip_address` |
| `RotatingAccessToken` | No — revocable only | `revoke_selected` | `id`, `user`, `family`, `issued_at`, `expires_at`, `revoked_at` |
| `RotatingRefreshToken` | No — read-only | — | `id`, `user`, `family`, `rotation_index`, `issued_at`, `used_at`, `reuse_detected_at` |
| `TokenFamily` | No — revocable only | `revoke_selected` | `id`, `user`, `created_at`, `last_rotated_at`, `expires_at`, `revoked_at`, `reuse_detected_at` |
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_AUTH_ACCESS_TOKEN_TTL_SECONDS` | Optional | how long each access token lasts before a rotation is needed. Range 60–86400. |
| `DJANGO_AUTH_REFRESH_TOKEN_TTL_SECONDS` | Optional | how long a token family lives before a full sign-in is required. Range 300–31536000. |
<!-- /generated:settings -->

```bash
DJANGO_OAUTH_MODE=rotation
DJANGO_AUTH_TOKEN_MODE=rotation
DJANGO_AUTH_ACCESS_TOKEN_TTL_SECONDS=3600
DJANGO_AUTH_REFRESH_TOKEN_TTL_SECONDS=1209600
```

The access TTL is how often a client rotates. The refresh TTL is the life of the
whole family -- after it, a real sign-in is required.

## Usage

### Rotating

```bash
curl -X POST .../auth/token/refresh -d '{"refresh_token": "eyJhbGciOi..."}'
```

```json
{
  "token_type": "bearer",
  "access_token": "eyJhbGciOi...",
  "refresh_token": "eyJhbGciOi...",
  "expires_in": 3600,
  "session_id": "8c1f..."
}
```

Both values are new. **Store the new refresh token and discard the old one
immediately** -- it is now spent, and presenting it again has consequences.

The `session_id` is the family, and it does not change across rotations.

### What happens on reuse

A refresh token is spendable exactly once. That turns a stolen token from a
permanent foothold into a race, and the loser of that race is how the theft
becomes visible -- because the only way a spent token can be presented again is if
somebody kept a copy.

When it happens:

1. The whole family is revoked, not just the token.
2. `reuse_detected_at` is stamped on the family.
3. A `RefreshTokenReuseEvent` records the IP, the user agent and a fingerprint of
   the presented digest.
4. Both the thief and the legitimate client get `401` and must sign in again.

```json
{"detail": "This session has been ended for your security."}
```

Ending the session for the real user too is not an oversight. At that moment the
two parties are indistinguishable, and the alternative -- guessing -- is worse.

Watch for it:

```python
from infrastructure.oauth.rotation.models import TokenFamily

TokenFamily.objects.filter(reuse_detected_at__isnull=False).count()
```

A steady trickle usually means a client with a race in its own refresh logic
rather than an attack. Two threads refreshing concurrently will produce exactly
this.

### Listing and ending sessions

```bash
curl .../auth/token/sessions -H 'Authorization: Bearer ...'
curl -X DELETE .../auth/token/sessions/8c1f... -H 'Authorization: Bearer ...'
```

`last_used_at` reports the last rotation.

## Notes

Read and write happen under one row lock, so two clients racing with the same
token cannot both walk away with a valid successor. Detection happens under that
lock; acting on it happens once the block has unwound, because the report is
delivered by raising and an exception thrown mid-transaction would roll back the
audit row and the family revocation with it.

The ancestry is kept: `parent`, `replaced_by` and `rotation_index` on each token,
which is what lets you reconstruct how far a compromised family got before it was
caught. `RotatingRefreshTokenInline` shows the chain under its family in the
admin.

Choose this mode when refresh-token theft matters. Choose
[session](session.md) if you would rather have a device list, or
[sliding](sliding.md) for the simplest thing that works.
