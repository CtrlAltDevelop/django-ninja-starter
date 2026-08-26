# Credentials and token modes

What a login hands back, and why it has two halves.

Every login method funnels through one function, `issue_credentials`. That is the
only place a credential is minted, which is what lets a password login, an SMS
code and a Google sign-in all produce the same thing — and what means adding a
method cannot accidentally skip the second factor or invent its own token format.

## The shape

```json
{
  "token_type": "bearer",
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expires_in": 3600,
  "session_id": "8c1f2b4e-..."
}
```

The access token goes in `Authorization: Bearer …`. The refresh token is presented
only at `/auth/token/refresh`. `session_id` names the row behind the credential and
is what `DELETE /auth/token/sessions/{id}` takes.

The `sliding` mode returns an empty `refresh_token`, because it has only one
token. The `none` mode returns `token_type: "session"` and a Django session key.

## Why a JWT *and* a database row

A self-contained token can be verified without a query. A self-contained token
also cannot be taken back. Both properties matter, so the design keeps both:

- The JWT carries `iss`, `sub`, `iat`, `nbf`, `exp`, `jti`, `typ`, `mode`, `sid`
  and `amr`. Verifying it answers **"we issued this and it has not expired"** with
  no database access at all, so a forged or stale token costs nothing to reject.
- The `jti` is the *only* secret in the token: 48 random bytes. The credential row
  is keyed by its digest. Looking it up answers **"and it has not since been
  revoked"**.

A request needs both answers. That is what keeps logout, rotation, reuse
detection and "sign out my other devices" immediate rather than eventually
consistent — none of which a stateless token can offer.

Two consequences worth knowing:

- **`typ` separates the pair.** A refresh token cannot authenticate an ordinary
  request, and an access token cannot be spent at the refresh endpoint. Both would
  otherwise verify perfectly well.
- **`mode` pins the table.** A token minted under a different token mode is
  refused rather than looked up, because its handle belongs to another table
  where at best it would not be found.

## Choosing a mode

`DJANGO_AUTH_TOKEN_MODE` picks one. Left unset, the default is worked out from
what else is enabled:

1. `DJANGO_OAUTH_MODE`, when it names a single mode.
2. Otherwise `rotation`, as soon as *anything* signs users in -- a login method, a
   second factor, or a social provider.
3. `none` only when nothing does.

The second rule is the one worth knowing. Enabling a login method and nothing
else used to leave this at `none`, which handed back a session cookie and an
empty `access_token`: a working login and an unusable API. A project that wants
Django sessions has to ask for them, because on an API that is the surprising
choice rather than the safe one.

Whichever mode is active, its app is installed for you. `DJANGO_OAUTH_MODE`
remains the way to install *several* modes' tables at once, which is what a
project migrating between them needs.

| Mode | Refresh means | Reach for it when |
| --- | --- | --- |
| [`sliding`](oauth/sliding.md) | Push the idle deadline out | You want sessions that quietly outlast a working day, and no refresh dance |
| [`session`](oauth/session.md) | Mint another access token against the same session | An account needs a device list it can act on |
| [`rotation`](oauth/rotation.md) | Spend the refresh token for a successor | Refresh-token theft is the threat you care about |
| `none` | — | You are serving a Django-session app and want no tokens at all |

Whichever you pick, the endpoints are the same:

```
POST   /api/v1/auth/token/refresh
POST   /api/v1/auth/token/revoke
GET    /api/v1/auth/token/sessions
DELETE /api/v1/auth/token/sessions/{session_id}
```

Only one mode's router is mounted, so a client never has to know which. Switching
modes changes what a deployment stores, not what its clients parse.

The mode's app has to be installed, which `DJANGO_OAUTH_MODE` controls. Asking for
`rotation` without it is a startup error, not a mystery at first login:

```
(auth.E001) DJANGO_AUTH_TOKEN_MODE=rotation needs the oauth_rotation app
    HINT: Set DJANGO_OAUTH_MODE to rotation or all.
```

## Signing

| Variable | Default | Notes |
| --- | --- | --- |
| `DJANGO_AUTH_JWT_ALGORITHM` | `HS256` | `HS256/384/512`, `RS256/384/512`, `ES256/384/512` |
| `DJANGO_AUTH_JWT_SIGNING_KEY` | — | Falls back to `SECRET_KEY` for HMAC only |
| `DJANGO_AUTH_JWT_VERIFYING_KEY` | — | The public key, for asymmetric algorithms |
| `DJANGO_AUTH_JWT_ISSUER` | project name | Carried as `iss` and verified on every request |
| `DJANGO_AUTH_JWT_AUDIENCE` | — | When set, carried as `aud` and verified |
| `DJANGO_AUTH_JWT_LEEWAY_SECONDS` | `30` | Clock tolerance |

`HS256` works out of the box from `SECRET_KEY`, which is why a fresh project
starts. Set `DJANGO_AUTH_JWT_SIGNING_KEY` anyway once you have somewhere to keep
it: re-keying tokens should not mean invalidating everything else `SECRET_KEY`
protects. A warning says so until you do.

An asymmetric algorithm has no sensible fallback — there is no private key to
derive from a shared secret — so a missing key pair is a hard error rather than a
quiet downgrade:

```
(auth.E008) RS256 signing needs: DJANGO_AUTH_JWT_SIGNING_KEY,
            DJANGO_AUTH_JWT_VERIFYING_KEY
    HINT: Supply the PEM-encoded key pair, or use HS256.
```

Reach for `RS256` or `ES256` when something other than this service has to verify
tokens: then the verifier needs only the public key, and the signing key never
leaves.

## Verifying in your own code

```python
from infrastructure.oauth.core import jwt_tokens

claims = jwt_tokens.decode(token, token_type=jwt_tokens.ACCESS)
claims.subject  # the user's primary key, as a string
claims.session_id  # the session, family or token id
claims.methods  # e.g. ["password", "totp"]
```

`decode` raises `JwtError` for anything it will not accept — malformed, expired,
signed by somebody else, or the wrong `typ`. It does **not** check revocation; for
that, use `api_auth` or `resolve_request_user`, which do both halves.

## Revoking

```python
from infrastructure.auth.core.sessions import revoke_all_for_user

revoke_all_for_user(account, reason="password_changed")
```

This sweeps all three mode tables rather than only the active one, because a
project that has switched modes still has yesterday's tokens sitting in
yesterday's table. It is what a password reset and a password change both call.
