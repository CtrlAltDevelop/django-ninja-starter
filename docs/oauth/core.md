# OAuth core

Clients, scopes, consents, social accounts, and the signing layer every token
mode is built on. Installed automatically as soon as any provider or token mode is
enabled; it has no routes of its own.

## Routes

<!-- generated:routes -->
_This app publishes no routes of its own._
<!-- /generated:routes -->

## Models

<!-- generated:models -->
#### `OAuthAuditEvent`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `event_type` | Char |  |
| `user` | ForeignKey | → `auth.User`, nullable |
| `client` | ForeignKey | → `oauth_core.OAuthClient`, nullable |
| `token_fingerprint` | Char |  |
| `ip_address` | GenericIPAddress | nullable |
| `user_agent` | Text |  |
| `metadata` | JSON |  |
| `created_at` | DateTime | not editable |

#### `OAuthAuthorizationCode`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `code_hash` | Char | unique, not editable |
| `user` | ForeignKey | → `auth.User` |
| `client` | ForeignKey | → `oauth_core.OAuthClient` |
| `redirect_uri` | Text |  |
| `scopes` | JSON |  |
| `audience` | Char |  |
| `code_challenge` | Char |  |
| `code_challenge_method` | Char |  |
| `nonce` | Char |  |
| `created_at` | DateTime | not editable |
| `expires_at` | DateTime |  |
| `consumed_at` | DateTime | nullable |
| `revoked_at` | DateTime | nullable |

#### `OAuthClient`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `name` | Char |  |
| `client_id` | Char | unique |
| `client_secret_hash` | Char | not editable |
| `client_type` | Char |  |
| `token_endpoint_auth_method` | Char |  |
| `redirect_uris` | JSON |  |
| `grant_types` | JSON |  |
| `response_types` | JSON |  |
| `audiences` | JSON |  |
| `is_first_party` | Boolean |  |
| `is_active` | Boolean |  |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |

#### `OAuthConsent`

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `user` | ForeignKey | → `auth.User` |
| `client` | ForeignKey | → `oauth_core.OAuthClient` |
| `granted_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `expires_at` | DateTime | nullable |
| `revoked_at` | DateTime | nullable |

#### `OAuthScope`

| Field | Type | Notes |
| --- | --- | --- |
| `name` | Char | primary key |
| `description` | Text |  |
| `is_default` | Boolean |  |
| `is_active` | Boolean |  |

#### `SocialLoginAttempt`

Single-use state for an outbound social authorization-code flow.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `provider` | Char |  |
| `state_hash` | Char | unique, not editable |
| `binding_hash` | Char | not editable |
| `nonce_hash` | Char | not editable |
| `code_verifier_encrypted` | Text | not editable |
| `user` | ForeignKey | → `auth.User`, nullable |
| `redirect_uri` | Char |  |
| `next_url` | Char |  |
| `requested_scopes` | JSON |  |
| `created_at` | DateTime | not editable |
| `expires_at` | DateTime |  |
| `consumed_at` | DateTime | nullable |
| `error` | Char |  |
| `ip_address` | GenericIPAddress | nullable |
| `user_agent` | Text |  |
<!-- /generated:models -->

`AbstractSocialAccount` and `AbstractOAuthToken` are the abstract bases each
provider and token mode inherits, which is why a linked Google account and a
linked GitHub account have identical columns without sharing a table. Separate
tables mean enabling one provider never puts another's rows in your schema.

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `OAuthAuditEvent` | No — read-only | — | `created_at`, `event_type`, `client`, `user`, `ip_address` |
| `OAuthAuthorizationCode` | No — read-only | — | `client`, `user`, `created_at`, `expires_at`, `consumed_at`, `revoked_at` |
| `OAuthClient` | Yes | — | `name`, `client_id`, `client_type`, `is_first_party`, `is_active`, `created_at` |
| `OAuthConsent` | Yes | — | `user`, `client`, `granted_at`, `expires_at`, `revoked_at` |
| `OAuthScope` | Yes | — | `name`, `is_default`, `is_active`, `description` |
| `SocialLoginAttempt` | No — read-only | — | `provider`, `created_at`, `expires_at`, `consumed_at`, `user`, `error` |
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_OAUTH_STATE_TTL_SECONDS` | Optional | how long a started social login may take to come back. Range 60–1800. |
| `DJANGO_OAUTH_HTTP_TIMEOUT_SECONDS` | Optional | how long to wait on a provider's token or profile endpoint. Range 0.1–60. |
| `DJANGO_OAUTH_CLOCK_SKEW_SECONDS` | Optional | the tolerance allowed when validating a provider's ID token. Range 0–300. |
<!-- /generated:settings -->

```bash
DJANGO_OAUTH_MODE=rotation
DJANGO_OAUTH_PROVIDERS=google,github
DJANGO_OAUTH_ENCRYPTION_KEY=<a Fernet key>
DJANGO_OAUTH_STORE_PROVIDER_TOKENS=false
```

`DJANGO_OAUTH_MODE` chooses which credential tables exist: `none`, `sliding`,
`session`, `rotation`, or `all`. See
[credentials and token modes](../credentials.md).

Provider tokens are only stored when `DJANGO_OAUTH_STORE_PROVIDER_TOKENS` is
true, and only encrypted. Leave it off unless you actually call provider APIs on
the user's behalf -- a token you do not keep cannot leak. When it is on, set
`DJANGO_OAUTH_ENCRYPTION_KEY` so those tokens can be re-keyed independently of
everything else `SECRET_KEY` protects.

## Usage

### The social login flow

Every provider exposes the same two endpoints:

```
GET /api/v1/oauth/{provider}/start      -> 302 to the provider
GET /api/v1/oauth/{provider}/callback   -> 302 to next_url, or 400 with a reason
```

Point a browser at `/start`. What happens next is the standard authorization-code
exchange, with a few things worth knowing about how it is guarded.

### What a pending attempt carries

`SocialLoginAttempt` holds one in-flight login. Nothing in it is readable:

- The `state` is stored as a digest, and is unique -- so a state cannot be
  replayed even within its TTL.
- The `nonce` is a digest, checked against the provider's ID token.
- The PKCE verifier is encrypted, not hashed, because it has to be sent back to
  the provider.
- A **binding** value is stored as a digest and set as an `httponly` cookie on the
  browser that started the login. The callback will not settle an attempt that
  came back to a different browser, which is what stops somebody handing you a
  URL that logs you into *their* account.

Providers that answer with a cross-site `form_post` -- Apple -- need
`SameSite=None` on that cookie, which browsers only honour when it is `Secure`.
Those providers are required to use HTTPS callbacks anyway, and the checks say so.

### Linking to an existing account

Call `/start` while already signed in and the provider identity is linked to that
account rather than resolving to a new one. An identity already linked elsewhere
is refused rather than moved.

### Redirect URIs

Set each provider's `*_OAUTH_REDIRECT_URI` to its exact registered callback. Left
empty, the callback is derived from the incoming request -- convenient locally,
and influenced by a spoofed `Host` header in production. Every provider warns
about this.

## Notes

ID tokens are verified properly: signature against the provider's JWKS, issuer,
audience, expiry with configurable clock skew, and the `azp` claim whenever the
audience is multi-valued. A missing subject identifier is refused rather than
stored as an empty string.

Provider-supplied profile fields are clipped to their column widths, so an
oversized display name is a truncated name rather than a database error mid-login.

## See also

- [Credentials and token modes](../credentials.md)
- [Google](google.md) - [Apple](apple.md) - [Microsoft](microsoft.md) - [GitHub](github.md)
