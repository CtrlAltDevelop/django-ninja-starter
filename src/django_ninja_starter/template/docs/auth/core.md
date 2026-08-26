# Authentication core

The records and machinery every login method shares. Installed automatically as
soon as any method or second factor is enabled; it has no routes of its own.

Three things live here rather than in the method apps, each for the same reason:
more than one method needs them.

- **`PhoneNumber`** is an identity, not an SMS-login detail. The SMS second
  factor needs the same verified number, and a project may enable either one
  without the other.
- **`AuthEvent`** is one audit trail rather than four, so "what happened to this
  account" is a single query.
- **The challenge store** is where a two-step login parks its state between step
  one and step two. Nothing about it is method-specific.

## Routes

<!-- generated:routes -->
_This app publishes no routes of its own._
<!-- /generated:routes -->

## Models

<!-- generated:models -->
#### `AuthEvent`

Audit trail for sign-in activity.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | BigAuto | primary key |
| `event_type` | Char |  |
| `method` | Char |  |
| `user` | ForeignKey | → `accounts.User`, nullable |
| `identifier_hash` | Char |  |
| `ip_address` | GenericIPAddress | nullable |
| `user_agent` | Text |  |
| `metadata` | JSON |  |
| `created_at` | DateTime | not editable |

#### `PhoneNumber`

A number an account has proven it controls.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `user` | ForeignKey | → `accounts.User` |
| `number` | Char | unique |
| `is_verified` | Boolean |  |
| `is_primary` | Boolean |  |
| `created_at` | DateTime | not editable |
| `verified_at` | DateTime | nullable |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `AuthEvent` | No — read-only | — | `created_at`, `event_type`, `method`, `user`, `ip_address` |
| `PhoneNumber` | Yes | — | `number`, `user`, `is_verified`, `is_primary`, `created_at`, `verified_at` |
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_AUTH_CHALLENGE_STORE` | **Yes** | where pending two-step logins are held. |
| `DJANGO_AUTH_REDIS_URL` | **Yes** | the Redis instance holding challenges and rate-limit counters. |
| `DJANGO_AUTH_CHALLENGE_TTL_SECONDS` | Optional | how long a delivered code stays redeemable. Range 60–3600. |
| `DJANGO_AUTH_CHALLENGE_MAX_ATTEMPTS` | Optional | how many wrong codes a challenge tolerates before it is destroyed. Range 1–20. |
| `DJANGO_AUTH_CODE_DIGITS` | Optional | the length of a one-time code. Range 4–10. |
| `DJANGO_AUTH_PENDING_LOGIN_TTL_SECONDS` | Optional | how long a login may wait between its first and second factor. Range 60–3600. |
| `DJANGO_AUTH_RESEND_COOLDOWN_SECONDS` | Optional | the wait before the same destination may be sent another code. Range 0–3600. |
| `DJANGO_AUTH_MAX_SENDS_PER_HOUR` | Optional | the hourly ceiling on codes to one destination. Range 0–1000. |
<!-- /generated:settings -->

### The challenge store

A challenge is the server-side half of a two-step sign-in. Step one mints a
ticket and delivers a code; step two trades the ticket plus the code for a
credential. Neither the ticket nor the code is ever stored in readable form: the
key is a digest of the ticket, and the code is fingerprinted with `SECRET_KEY` as
the HMAC key, salted with the ticket and the purpose. A six-digit code has far
too little entropy to survive a bare digest, so recovering one needs the key as
well as a dump of the store.

Two implementations ship:

| Store | Use |
| --- | --- |
| `infrastructure.auth.core.challenges.RedisChallengeStore` | The default, and the only one fit for production. |
| `infrastructure.auth.core.challenges.LocMemChallengeStore` | Tests and single-worker development. Warns about itself. |

The in-memory store is deliberately not safe across processes: a multi-worker
deployment would hand step two to a worker that never saw step one. The system
check says so every time it is configured.

A project can supply its own by dotted path -- the store is a `Protocol`, not a
base class, so nothing needs subclassing.

### Delivery backends

Codes and links leave through swappable backends, so bolting on Twilio or a
transactional email vendor never touches the flows that call them.

| Setting | Ships with |
| --- | --- |
| `DJANGO_AUTH_SMS_BACKEND` | `ConsoleSmsBackend` (logs), `LocMemSmsBackend` (tests) |
| `DJANGO_AUTH_EMAIL_BACKEND` | `DjangoEmailBackend` (honours `EMAIL_BACKEND`), `ConsoleEmailBackend`, `LocMemEmailBackend` |

The console SMS backend writes codes to the log, which means anyone who can read
logs can sign in as anyone. It is the default because it needs no account, and
the checks warn about it whenever SMS is actually in use.

### Rate limits

Counters live in the same store as the challenges, so limits hold across every
worker rather than per process.

- `DJANGO_AUTH_RESEND_COOLDOWN_SECONDS` -- the wait before the same destination
  may be sent another code. Charged *before* the message is handed to a carrier,
  so a provider outage cannot be turned into an unmetered send loop.
- `DJANGO_AUTH_MAX_SENDS_PER_HOUR` -- the hourly ceiling per destination.
- `DJANGO_AUTH_CHALLENGE_MAX_ATTEMPTS` -- wrong codes tolerated before the
  challenge is destroyed outright.

## Usage

### Enabling methods

```bash
DJANGO_AUTH_METHODS=password,email_code,magic_link
DJANGO_AUTH_SECOND_FACTORS=totp,recovery
```

Each name installs its app and mounts its router. Nothing is installed and no
routes exist for a name that is absent, so an unused method costs nothing --
not a table, not an endpoint, not a settings requirement.

An unknown name is refused at startup rather than ignored:

```
django.core.exceptions.ImproperlyConfigured: Unknown DJANGO_AUTH_METHODS: passwrod
```

### Reading the audit trail

```python
from infrastructure.auth.core.models import AuthEvent, AuthEventType

AuthEvent.objects.filter(
    user=account,
    event_type=AuthEventType.LOGIN_FAILED,
).count()
```

Identifiers are stored as digests, so the trail can answer "how many failures hit
this address" without becoming a second copy of the user table. Query by account
where you have one, and by `identifier_hash` where you only have an address:

```python
from infrastructure.auth.core.throttling import fingerprint

AuthEvent.objects.filter(identifier_hash=fingerprint("someone@example.com"))
```

### Protecting your own endpoints

```python
from ninja import Router
from infrastructure.auth.core.sessions import api_auth

router = Router()


@router.get("/me", auth=api_auth)
def me(request):
    return {"id": request.user.pk}
```

`api_auth` verifies the signature before touching the database, confirms the
credential row is still live, and puts the account on `request.user`. It is a
bearer *scheme*, so it also appears in the OpenAPI document and Swagger's
Authorize button works.

## See also

- [Credentials and token modes](../credentials.md) -- what a login hands back.
- [Two-factor authentication](twofactor.md) -- what happens when a first factor
  is not enough.
