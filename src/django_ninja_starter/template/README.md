# {{ project_title }}

A production-oriented API built with Django and Django Ninja.

Authentication is included and opt-in: login methods, second factors, social providers
and token modes are each a separate app that installs nothing until you name it. Every
login ends by minting a signed JWT.

A CMS is included and opt-in like everything else: name it in
`DJANGO_CMS_ENABLED` and you get pages made of sections made of typed, translatable
fields, shared sections, menus, drafts with preview links, and an admin screen built
for whoever writes the copy rather than for whoever wrote the models. Leave it unset
and the project carries no CMS tables, routes or admin at all.

Notifications come the same way: name them in `DJANGO_NOTIFICATIONS_ENABLED` and
you get a notification table, a read API and a WebSocket that pushes new ones the
moment they are created. The connection is useful before it is authenticated —
anyone who connects hears what was addressed to everybody, and sending a token
over the same socket adds that account's own feed to it.

The admin is themed with [Unfold](https://unfoldadmin.com) throughout: a dashboard of
real numbers instead of a list of models, a sidebar built from the apps you actually
installed, and an environment badge so nobody edits production by mistake.

**[Read the documentation](docs/README.md)** — one page per app, covering its routes,
models, admin, setup and usage.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
cp .env.example .env
make migrate
make run
```

Open <http://127.0.0.1:8000/api/docs> for interactive API documentation. Use Swagger's
top-bar selector to switch between registered API versions.

## Create a versioned API

```bash
python manage.py startapi users --api-version v1
```

The command creates `src/apps/users/api/v1.py` with an endpoint test, registers the app and router, exposes
`GET /api/v1/users/`, and adds the API to Swagger automatically. Additional examples:

```bash
python manage.py startapi users --api-version v2
python manage.py startapi reports --api-version v2 --prefix /internal-reports
```

## Project layout

```text
src/
├── apps/                   # Feature applications: yours, and the two that ship
│   ├── cms/                # Pages, sections and typed multilingual content
│   └── notifications/      # Stored notifications, a read API, and a WebSocket
├── infrastructure/
│   ├── common/             # Project-owned foundation application
│   ├── accounts/           # The user model and the profile attached to it
│   ├── auth/               # Login methods and second factors
│   │   ├── core/           # Challenge store, delivery, throttling, credential issuance
│   │   ├── password/       # Username-or-email and password
│   │   ├── email_code/     # One-time code by email
│   │   ├── sms_code/       # One-time code by SMS
│   │   ├── magic_link/     # Single-use emailed link
│   │   └── twofactor/      # TOTP, SMS, email, and recovery second factors
│   └── oauth/
│       ├── core/           # Shared clients, scopes, consent, PKCE, and audit models
│       ├── google/         # Social sign-in providers, one app each
│       ├── apple/
│       ├── microsoft/
│       ├── github/
│       ├── sliding/        # Sliding token mode
│       ├── session/        # Server-side session mode
│       └── rotation/       # Access/refresh rotation mode
└── config/
    ├── settings/           # Base, development, test, and production settings
    ├── api.py              # Versioned NinjaAPI composition root
    ├── urls.py             # Where an HTTP request is routed
    ├── sockets.py          # Where a WebSocket connection is routed
    ├── asgi.py
    └── wsgi.py
```

Create business features under `src/apps/`. Keep project foundation code and technical
integrations under `src/infrastructure/`.

## OAuth storage modes

Set `DJANGO_OAUTH_MODE` to choose one independently installable model set:

| Mode | Behavior |
| --- | --- |
| `none` | OAuth storage is disabled (default) |
| `sliding` | One opaque token with sliding idle expiry and an absolute lifetime |
| `session` | Server-side sessions with revocable access tokens |
| `rotation` | Access/refresh pairs with ancestry, rotation, and reuse-family revocation |
| `all` | Enable every model set |

Every active mode includes the shared scope, client, consent, hashed PKCE authorization-code,
and audit models from `oauth_core`. Raw tokens and client secrets must never be persisted; store
only `hash_token(value)`. These apps provide the persistence/domain layer, ready for the project
to expose through its own issuance and authentication routes.

Choose the mode before applying migrations:

```bash
DJANGO_OAUTH_MODE=rotation python manage.py migrate
```

## Social OAuth providers

Enable the independently installable provider apps you need:

```env
DJANGO_OAUTH_PROVIDERS=google,apple,microsoft,github
```

Each provider exposes `/api/<version>/oauth/<provider>/start` and `/callback` in Swagger. Google,
Microsoft, and GitHub use a GET callback; Apple uses `POST` with `form_post`. Configure the matching
`GOOGLE_OAUTH_*`, `APPLE_OAUTH_*`, `MICROSOFT_OAUTH_*`, or `GITHUB_OAUTH_*` variables in `.env`,
including an exact registered redirect URI.

Accounts are linked by stable provider subject, not email. State is single-use and hashed; OIDC
nonces, PKCE, signature/audience/issuer validation, encrypted transient credentials, and provider
timeouts are built in. `start` also sets a short-lived `HttpOnly` binding cookie and stores only its
hash, so a state that comes back from a different browser is refused rather than signing that
browser in. Apple's cross-site `form_post` callback needs `SameSite=None; Secure` for that cookie,
which is why it requires an HTTPS callback. Upstream tokens are discarded unless
`DJANGO_OAUTH_STORE_PROVIDER_TOKENS=true`; when retained, they are encrypted using
`DJANGO_OAUTH_ENCRYPTION_KEY` or a key derived from `DJANGO_SECRET_KEY`.

Microsoft tenant configuration accepts `common`, `organizations`, `consumers`, or a tenant GUID.
Run `python manage.py check` to catch missing credentials, invalid callback schemes, and unsafe
OAuth timeout, state-lifetime, or clock-skew settings before deployment.

## Login methods and two-factor authentication

Enable the login methods and second factors the project needs:

```env
DJANGO_AUTH_METHODS=password,email_code,sms_code,magic_link
DJANGO_AUTH_SECOND_FACTORS=totp,sms,email,recovery
```

| Method | Routes under `/api/<version>` |
| --- | --- |
| `password` | `POST /auth/password/{signup,login,logout,forgot,reset,change}` |
| `email_code` | `POST /auth/email-code/{signup/start,signup/verify,login/start,login/verify,logout}` |
| `sms_code` | `POST /auth/sms-code/{signup/start,signup/verify,login/start,login/verify,logout}` |
| `magic_link` | `POST /auth/magic-link/{signup/start,login/start,verify,logout}` |

Every method answers with the same shape, so the two-step branch is written once:

```jsonc
{"requires_second_factor": false, "credentials": {"token_type": "bearer", "access_token": "..."}}
{"requires_second_factor": true, "login_ticket": "...", "methods": ["totp"]}
```

Both are the `data` of [the response envelope](docs/responses.md).

When a second factor is required, post the ticket and a code to `POST /auth/2fa/verify`. For
`sms` and `email` factors, call `POST /auth/2fa/challenge` first to have a code sent — it returns
the same ticket, so only one is ever tracked. Manage enrolment through `/auth/2fa/totp/enroll`,
`/totp/confirm`, `/sms/enroll`, `/sms/confirm`, `/email/enroll`, `/email/confirm`,
`/recovery/generate`, `GET /auth/2fa/methods`, and `DELETE /auth/2fa/{method}`.

Pending logins and one-time codes live in Redis (`DJANGO_AUTH_REDIS_URL`), never the database.
Tickets are stored as SHA-256 of themselves and codes as an HMAC keyed with `DJANGO_SECRET_KEY`,
so a dump cannot be replayed. Codes are single use, attempt-capped, bound to the purpose that
minted them, and rate-limited per destination. `LocMemChallengeStore` is for tests and
single-worker development only — it does not survive across processes, and `check` warns about it.

`DJANGO_AUTH_TOKEN_MODE` decides which OAuth storage mode issues the credential, so password,
passwordless, and social logins all produce the same records and share one revocation path. It
follows `DJANGO_OAUTH_MODE` when that names one, and is otherwise `rotation` as soon as anything
signs users in -- so enabling a login method gets you a real bearer token, not a session cookie.
Ask for `none` explicitly to use a Django session instead.
Changing or resetting a password revokes every live credential for that account.

SMS and email transports are swapped by dotted path (`DJANGO_AUTH_SMS_BACKEND`,
`DJANGO_AUTH_EMAIL_BACKEND`). The defaults log rather than send; point them at a real carrier
before deploying. `magic_link` additionally requires `DJANGO_AUTH_MAGIC_LINK_BASE_URL`.

Sign-in endpoints answer identically whether or not an account exists, mask destinations in
responses, and record a hashed audit trail in `AuthEvent`.

Each app declares the settings it cannot work without, so `python manage.py check` names
exactly what the apps you enabled still need:

```
ERRORS:
?: (auth_magic_link.AUTH_MAGIC_LINK_BASE_URL) Magic-link login needs
   AUTH_MAGIC_LINK_BASE_URL: the page that reads the token out of the URL
   HINT: Set DJANGO_AUTH_MAGIC_LINK_BASE_URL.
```

Each method, factor, provider and token mode has its own page under [`docs/`](docs/README.md).
Start with [credentials and token modes](docs/credentials.md).

## Commands

```bash
make check       # lint, format, types, Django checks, and migration drift
make test        # tests with branch coverage
make docs        # regenerate the reference sections of docs/
make migrations  # create migrations
make migrate     # apply migrations
make superuser   # create an admin user
make run         # start the development server (WSGI: no WebSocket)
make serve       # start an ASGI server, which does serve the WebSocket
```

`make run` is `manage.py runserver`, which is WSGI and will never serve a
WebSocket — the connection simply never opens. Use `make serve` when
notifications are enabled; it needs the `asgi` extra that `dev` already pulls in.

## Configuration

Development uses SQLite by default. Copy `.env.example` to `.env` and set the `DJANGO_*`
variables for another database or production deployment. Production uses
`config.settings.production` and requires a secure `DJANGO_SECRET_KEY` and non-empty
`DJANGO_ALLOWED_HOSTS`.

## One shape for every response

Every JSON body — a payload, a refusal, a validation failure — arrives in the same
envelope, so a client parses it once:

```json
{
  "errors": null,
  "data": { "token_type": "bearer", "access_token": "eyJhbGciOi..." },
  "isSuccess": true,
  "statusCode": 200,
  "title": "SUCCESS",
  "description": "The request succeeded."
}
```

`title` is a member of a fixed enum — `INVALID_CREDENTIALS`, `CODE_EXPIRED`,
`TOKEN_REUSED` — so a client keys its own translations off it instead of showing
an English sentence to everyone. Endpoints go on returning their own schemas; the
wrapping happens once, at the renderer, and `/api/docs` documents it.
[Full reference](docs/responses.md).

## Included endpoints

- `GET /api/v1/health/live` — process liveness
- `GET /api/v1/health/ready` — database readiness
- `GET /api/docs` — Swagger documentation with an API-version selector
- `GET /api/<version>/openapi.json` — version-specific OpenAPI schema
- `WS /ws/notifications` — the notification feed, when notifications are enabled
  and the project is served by `make serve` rather than `make run`
