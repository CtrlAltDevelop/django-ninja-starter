# Django Ninja Starter

A production-oriented Django and Django Ninja starter, available as both a GitHub
Template and an installable Python project generator.

It includes environment-specific settings, secure production defaults, health checks,
OpenAPI documentation, tests, typing, linting, coverage, CI, and a feature-first source
layout.

Authentication is included and opt-in: four login methods, four second factors, four
social providers and three token modes, each a separate app that installs nothing until
you name it. Every login ends by minting a signed JWT.

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

## Requirements

- Python 3.12 or newer
- Django 5.2 or newer (Django 6.x is supported)

## Create a project

### Option 1: Python package

After the package is published to PyPI, install the generator with `pipx` and create a
project:

```bash
pipx install django-ninja-starter
django-ninja-starter my-api
cd my-api
```

To use the package directly from this checkout before publishing:

```bash
pipx install .
django-ninja-starter my-api
```

Choose a different output directory when needed:

```bash
django-ninja-starter my-api --directory ./services/my-api
```

The generator refuses to overwrite a non-empty directory.

### Option 2: GitHub Template

Repository administrators must enable **Settings → General → Template repository** once.
Users can then select **Use this template**, create a new repository, and clone it. GitHub
copies the default branch files into the new repository with an independent history.

After creating a repository from the template, update the project `name` and `description`
in `pyproject.toml`, then follow the setup below.

## Set up the generated project

```bash
python3 -m venv .venv
source .venv/bin/activate
make install
cp .env.example .env
make migrate
make run
```

Open <http://127.0.0.1:8000/api/docs> for interactive API documentation. Swagger's
top bar lets you select any registered API version.

If you are signed into the admin as a staff user, the page authorises itself:
under every token mode but `none` the API reads `Authorization` and ignores
cookies, so an admin session would otherwise get a `401` from **Try it out**. The
page trades that session for a real token and fills **Authorize** in. It leaves
the session alone, it is staff-only, and
`DJANGO_AUTH_SESSION_TOKEN_FOR_STAFF=false` removes the route entirely. A line
above the topbar says which of those you got — authorised as whom, or not signed
in, or signed in without staff — so the page never looks the same whether it
worked or not.
[How it works](docs/signing-in.md#trying-the-api-out-from-the-admin).

## Create a versioned API

Use the included Django management command to scaffold and register a feature API:

```bash
python manage.py startapi users --api-version v1
```

This creates `src/apps/users/api/v1.py` and a matching endpoint test, then adds the app and router to
`src/config/api_registry.json`, and exposes the example endpoint at
`GET /api/v1/users/`. It appears automatically in both the v1 OpenAPI schema and the
Swagger version selector at `/api/docs`.

Create another API version without duplicating the feature app:

```bash
python manage.py startapi users --api-version v2
python manage.py startapi reports --api-version v2 --prefix /internal-reports
```

Versions accept `v1`, `v1.1`, or `v1.1.0`. App names use lowercase Python identifiers,
such as `users` or `order_items`. The command refuses to overwrite an existing version or
register a duplicate prefix.

## See it running

The repository builds its own example project and tours it, offline and with no
setup beyond the dev install:

```bash
python examples/walkthrough.py
```

That builds `example-api` with the packaged generator, copies
`examples/env.example` in as its `.env` — every login method, second factor,
social provider and token mode on, plus the CMS and notifications — registers
the `notes` feature app at v1 and v2 through `manage.py startapi`, and then
prints a transcript of the calls a real client would make against it. The tour
covers the WebSocket too, by calling the ASGI application directly rather than
starting a server, so it needs no uvicorn and no open port.
`examples/README.md` has the details, and `tests/test_example_project.py` runs
the whole thing in CI, so the example cannot drift from the package that
generates it.

## Architecture

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

Create business features under `src/apps/`. Keep foundational code and technical
integrations under `src/infrastructure/`. `config/api.py` mounts feature routers and should
remain free of business logic.

The recommended dependency direction is `transport → services → models/integrations`.
Models should not import API schemas or routers. Cross-feature workflows belong in explicit
services unless event semantics are intentional.

## OAuth storage modes

Select one independently installable token model with `DJANGO_OAUTH_MODE`:

| Mode | Stored models | Intended behavior |
| --- | --- | --- |
| `none` | None | OAuth storage is disabled (default) |
| `sliding` | `SlidingToken`, `SlidingTokenEvent` | One opaque bearer token whose idle expiry advances up to an absolute limit |
| `session` | `OAuthSession`, `SessionAccessToken`, `SessionRevocation` | Server-side device sessions with access-token and whole-session revocation |
| `rotation` | `TokenFamily`, rotating access/refresh tokens, reuse events | Every refresh replaces its parent; reuse revokes the entire token family |
| `all` | All models above | Development or projects that deliberately support every mode |

Every enabled mode also installs `oauth_core`, which provides scopes, public/confidential
clients, consent, hashed PKCE authorization codes, and audit events. Token and client-secret
values are intentionally never stored in plaintext: generate a value once, return it to the
caller, and persist only `hash_token(value)`. The included code is the persistence and domain
model layer; projects should add their own issuance/authentication routes and policies around it.

Set the mode before migrations:

```bash
DJANGO_OAUTH_MODE=rotation python manage.py migrate
```

Keep the selected mode stable for a deployed database. Run `makemigrations` and `migrate` if
you later change it, and explicitly plan how existing credentials will be revoked or migrated.

## Social OAuth providers

Google, Apple, Microsoft, and GitHub are separate infrastructure apps. Enable only the ones a
project uses:

```env
DJANGO_OAUTH_PROVIDERS=google,apple,microsoft,github
```

Each enabled provider adds these versioned routes and OpenAPI entries:

- `GET /api/<version>/oauth/<provider>/start` — create protected state and redirect to consent
- `GET /api/<version>/oauth/<provider>/callback` — Google, Microsoft, and GitHub callback
- `POST /api/<version>/oauth/apple/callback` — Apple's `form_post` callback

Provider accounts are linked by the provider's stable subject identifier, never automatically by
email. A callback signs in an existing linked account, links it to the authenticated user who
started the flow, or creates a local user when `DJANGO_OAUTH_AUTO_CREATE_USERS=true`. Set
`DJANGO_OAUTH_USER_RESOLVER` to a dotted callable for custom user provisioning.

Required credentials:

| Provider | Required variables |
| --- | --- |
| Google | `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET` |
| Apple | `APPLE_OAUTH_CLIENT_ID`, `APPLE_OAUTH_TEAM_ID`, `APPLE_OAUTH_KEY_ID`, `APPLE_OAUTH_PRIVATE_KEY` |
| Microsoft | `MICROSOFT_OAUTH_CLIENT_ID`, `MICROSOFT_OAUTH_CLIENT_SECRET`; tenant is `common`, `organizations`, `consumers`, or a tenant GUID |
| GitHub | `GITHUB_OAUTH_CLIENT_ID`, `GITHUB_OAUTH_CLIENT_SECRET` |

Set each provider's `*_OAUTH_REDIRECT_URI` to its exact registered callback URL. Apple requires an
HTTPS domain and posts its callback. The flow uses single-use hashed state, OIDC nonce validation,
PKCE where the provider supports it, signed Apple client secrets, validated ID-token signatures,
audiences and issuers, and short network timeouts.

`start` also sets a short-lived `HttpOnly` binding cookie and stores only its hash on the attempt.
The callback refuses a state that arrives from a different browser, so a stolen `state`/`code` pair
cannot be replayed into a victim's browser to sign them into the attacker's account. Apple's
`form_post` callback is cross-site, so its binding cookie is issued as `SameSite=None; Secure`;
the redirect-based providers use `SameSite=Lax`.

`python manage.py check` reports enabled providers with missing credentials, invalid Apple callback
schemes, ambiguous Microsoft tenants, and unsafe timeout/state/clock-skew limits before deployment.

Upstream access and refresh tokens are not retained by default. Set
`DJANGO_OAUTH_STORE_PROVIDER_TOKENS=true` only when the application needs provider APIs. Stored
tokens and transient PKCE verifiers are encrypted with `DJANGO_OAUTH_ENCRYPTION_KEY`; when empty,
a key is derived from `DJANGO_SECRET_KEY`. Changing either key requires a credential migration or
provider reauthorization.

## Login methods and two-factor authentication

Each login method is a separate infrastructure app. Enable only what a project uses:

```env
DJANGO_AUTH_METHODS=password,email_code,sms_code,magic_link
DJANGO_AUTH_SECOND_FACTORS=totp,sms,email,recovery
```

Every method mounts under `/api/<version>/auth/<method>` and answers with the same shape, so a
client writes the two-step branch once:

| Method | Routes |
| --- | --- |
| `password` | `POST /auth/password/{signup,login,logout,forgot,reset,change}` |
| `email_code` | `POST /auth/email-code/{signup/start,signup/verify,login/start,login/verify,logout}` |
| `sms_code` | `POST /auth/sms-code/{signup/start,signup/verify,login/start,login/verify,logout}` |
| `magic_link` | `POST /auth/magic-link/{signup/start,login/start,verify,logout}` |

A successful first factor returns either a credential or a ticket:

```jsonc
// no second factor enrolled
{"requires_second_factor": false, "credentials": {"token_type": "bearer", "access_token": "..."}}

// second factor required
{"requires_second_factor": true, "login_ticket": "...", "methods": ["totp"]}
```

Both are the `data` of [the response envelope](docs/responses.md).

The client then posts the ticket and a code to `POST /auth/2fa/verify`. For `sms` and `email`
factors it first calls `POST /auth/2fa/challenge` to have a code sent; that returns the *same*
ticket, so the client only ever tracks one. Enrolment lives at `POST /auth/2fa/totp/enroll`,
`/totp/confirm`, `/sms/enroll`, `/sms/confirm`, `/email/enroll`, `/email/confirm`, and
`/recovery/generate`, with `GET /auth/2fa/methods` and `DELETE /auth/2fa/{method}` to manage
what is enabled.

### Two-step state lives in Redis, hashed

Pending logins and one-time codes never touch the database. `DJANGO_AUTH_CHALLENGE_STORE`
selects the backend; the default keeps them in Redis under `DJANGO_AUTH_REDIS_URL` and lets the
TTL expire them.

Nothing is stored in a form that can be replayed from a dump. A ticket is stored only as the
SHA-256 of itself, and a code as an HMAC keyed with `DJANGO_SECRET_KEY` and salted with the
ticket and purpose — a six-digit code has far too little entropy to survive a bare digest.
Tickets are bound to the purpose that minted them, so a sign-up ticket cannot be spent on a
login. Codes are single use, capped at `DJANGO_AUTH_CHALLENGE_MAX_ATTEMPTS` guesses, and
rate-limited per destination by `DJANGO_AUTH_RESEND_COOLDOWN_SECONDS` and
`DJANGO_AUTH_MAX_SENDS_PER_HOUR`.

The pending-login ticket is read rather than consumed during 2FA, so a mistyped authenticator
code does not send the user back to the password prompt; repeated failures still retire it.
Authenticator codes record the time step they were accepted at and are refused a second time,
and a recovery code is only ever spent when asked for by name — never inferred from a wrong TOTP
code.

`LocMemChallengeStore` exists for tests and single-worker development. It does not survive
across processes, so a multi-worker deployment would hand step two to a worker that never saw
step one; `python manage.py check` warns when it is configured.

### Credentials come from the OAuth token modes

Authentication does not invent its own token format. `DJANGO_AUTH_TOKEN_MODE` picks which of the
storage modes above issues the credential, so a password login and a social login produce the
same records and share one revocation story. Every method ends by minting a signed JWT, and a
token from any one of them is accepted by every endpoint in the project.

Left unset, the mode follows `DJANGO_OAUTH_MODE` when that names one; otherwise it is `rotation`
as soon as anything signs users in, and `none` only when nothing does. Enabling just a login
method therefore gets you a real bearer token rather than a session cookie and an empty
`access_token`. Ask for `none` explicitly to sign in with a Django session instead. The active
mode's app is installed for you, and `check` reports a mode whose app is missing.

Changing a password or completing a reset revokes every live credential for that account across
all three mode tables.

### Delivery backends

SMS and email are swapped by dotted path through `DJANGO_AUTH_SMS_BACKEND` and
`DJANGO_AUTH_EMAIL_BACKEND`. The defaults never reach the network: SMS goes to the log, email
goes through whatever Django is already configured to use. Point them at a carrier for anything
real — `check` warns when SMS codes are being written to a log.

### What the endpoints deliberately do not reveal

`login/start` and `signup/start` behave identically whether or not an address or number has an
account, and `password/forgot` returns a ticket either way — for an unknown address that ticket
is a decoy bound to a code that was never sent. Responses mask where a code went
(`z***@example.com`, `***0101`) rather than echoing it back. A password login runs a throwaway
hash when no account matches, so a missing identifier costs the same time as a wrong password.

Phone numbers, verified addresses, enrolled factors, and a hashed audit trail (`AuthEvent`) are
stored under `infrastructure/auth/core`; identifiers in the audit log are digests, not a second
user table.

Each method, factor, provider and token mode has its own page under [`docs/`](docs/README.md)
covering its routes, models, admin, setup and usage. Start with
[credentials and token modes](docs/credentials.md).

## Feature apps

Two applications ship in `src/apps/` rather than in `src/infrastructure/`, because
they are features a project chooses rather than plumbing under it. Both are opt-in
the same way a login method is, both live entirely in their own directory, and
neither imports anything from the project around it — so either can be copied into
another Django project or deleted from this one without leaving a hole.

| App | Enabled by | What you get |
| --- | --- | --- |
| [`cms`](docs/cms.md) | `DJANGO_CMS_ENABLED=true` | Pages made of sections made of typed, translatable fields; a library of shared sections; menus; drafts, schedules and signed preview links; export/import for moving content between environments; and a content-editing admin screen separate from the structural one |
| [`notifications`](docs/notifications.md) | `DJANGO_NOTIFICATIONS_ENABLED=true` | A notification table addressed to one account or to everybody, per-account read receipts, a scoped read API, and a WebSocket that pushes new ones on save |

Both are toured end to end by `python examples/walkthrough.py`.

### The notification socket needs an ASGI server

`manage.py runserver` is WSGI and will never serve a WebSocket — the connection
simply never opens, which is a confusing way to find out. The project ships
`make serve` for this, which is `uvicorn config.asgi:application --reload
--app-dir src` and needs the `asgi` extra that `dev` already pulls in.

`config/sockets.py` is where a `websocket` scope is routed, deliberately the
project's file rather than an app's: an app publishes a socket application the way
it publishes a router, and the project decides whether it is mounted and where. A
path with nothing on it is closed with code `4404` rather than left hanging.

In production, set `DJANGO_NOTIFICATIONS_BROKER` to
`apps.notifications.broadcast.RedisBroker`. The default `MemoryBroker` fans out
inside a single process, so under two workers a client connected to the first
never hears about a notification created by the second; `manage.py check` warns
while it is still in place.

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

- `GET /api/v1/health/live` — confirms that the web process is serving requests
- `GET /api/v1/health/ready` — confirms that the database is available
- `GET /api/docs` — Swagger documentation with an API-version selector
- `GET /api/<version>/docs` — Swagger documentation opened on a specific version
- `GET /api/<version>/openapi.json` — OpenAPI schema for a specific version
- `WS /ws/notifications` — the notification feed, when notifications are enabled.
  An ASGI server is required; `manage.py runserver` is WSGI and will never serve
  it. Use `make serve`.

## Configuration

Development uses SQLite by default. Copy `.env.example` to `.env` and configure these
variables as needed:

| Variable | Purpose | Default |
| --- | --- | --- |
| `DJANGO_SETTINGS_MODULE` | Active settings module | `config.settings.development` |
| `DJANGO_SECRET_KEY` | Django signing key | Unsafe development value |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames | `localhost,127.0.0.1` in `.env.example` |
| `DJANGO_OAUTH_MODE` | OAuth model set: `none`, `sliding`, `session`, `rotation`, or `all` | `none` |
| `DJANGO_OAUTH_PROVIDERS` | Comma-separated `google`, `apple`, `microsoft`, and/or `github` | Empty |
| `DJANGO_OAUTH_ENCRYPTION_KEY` | Fernet key for recoverable provider credentials | Derived from secret key |
| `DJANGO_OAUTH_STORE_PROVIDER_TOKENS` | Persist encrypted upstream tokens | `false` |
| `DJANGO_OAUTH_CLOCK_SKEW_SECONDS` | Allowed ID-token clock skew, from 0 through 300 | `60` |
| `DJANGO_AUTH_METHODS` | Comma-separated `password`, `email_code`, `sms_code`, and/or `magic_link` | Empty |
| `DJANGO_AUTH_SECOND_FACTORS` | Comma-separated `totp`, `sms`, `email`, and/or `recovery` | Empty |
| `DJANGO_AUTH_TOKEN_MODE` | Which mode issues credentials: `none`, `sliding`, `session`, `rotation` | `DJANGO_OAUTH_MODE` |
| `DJANGO_AUTH_REDIS_URL` | Redis holding pending logins and one-time codes | `redis://127.0.0.1:6379/0` |
| `DJANGO_AUTH_CHALLENGE_STORE` | Dotted path to the challenge backend | `RedisChallengeStore` |
| `DJANGO_AUTH_CHALLENGE_TTL_SECONDS` | Code lifetime, from 60 through 3600 | `300` |
| `DJANGO_AUTH_CHALLENGE_MAX_ATTEMPTS` | Guesses allowed per code, from 1 through 20 | `5` |
| `DJANGO_AUTH_PENDING_LOGIN_TTL_SECONDS` | How long a login may wait on its second factor | `600` |
| `DJANGO_AUTH_CODE_DIGITS` | One-time code length, from 4 through 10 | `6` |
| `DJANGO_AUTH_RESEND_COOLDOWN_SECONDS` | Minimum gap between sends to one destination | `30` |
| `DJANGO_AUTH_MAX_SENDS_PER_HOUR` | Hourly send ceiling per destination | `10` |
| `DJANGO_AUTH_SMS_BACKEND` | Dotted path to the SMS transport | Console (logs only) |
| `DJANGO_AUTH_EMAIL_BACKEND` | Dotted path to the email transport | Django mail |
| `DJANGO_AUTH_MAGIC_LINK_BASE_URL` | Page that reads the token out of the link | Empty (required for `magic_link`) |
| `DJANGO_AUTH_PASSWORD_RESET_BASE_URL` | Page that reads a reset ticket | Empty |
| `DJANGO_AUTH_AUTO_CREATE_USERS` | Create an account on first passwordless sign-in | `true` |
| `DJANGO_AUTH_TOTP_ISSUER` | Name shown in authenticator apps | `Django Ninja Starter` |
| `DJANGO_AUTH_RECOVERY_CODE_COUNT` | Codes issued per batch, from 5 through 30 | `10` |
| `DJANGO_AUTH_SESSION_TOKEN_FOR_STAFF` | Let a staff session be traded for a bearer token, which is what lets `/api/docs` authorise itself | `true` |
| `DJANGO_CMS_ENABLED` | Install the CMS: its tables, routes and admin | `false` |
| `DJANGO_CMS_LANGUAGES` | Languages content may be written in, most preferred first | `LANGUAGE_CODE` |
| `DJANGO_CMS_PREVIEW_TTL_SECONDS` | How long a preview link opens a draft for | `86400` |
| `DJANGO_NOTIFICATIONS_ENABLED` | Install notifications: tables, routes and the WebSocket | `false` |
| `DJANGO_NOTIFICATIONS_BROKER` | Dotted path to the fan-out backend | `MemoryBroker` (one process only) |
| `DJANGO_NOTIFICATIONS_REDIS_URL` | Redis backing `RedisBroker` | `DJANGO_AUTH_REDIS_URL` |
| `DJANGO_NOTIFICATIONS_WS_PATH` | Path the notification socket is mounted at | `/ws/notifications` |
| `DJANGO_NOTIFICATIONS_CHANNEL_PREFIX` | Namespace for the broker's channels | `notifications` |
| `DJANGO_NOTIFICATIONS_SOCKET_BACKLOG` | Unread a client is caught up with on connect, 0 through 500 | `20` |
| `DJANGO_DB_ENGINE` | Django database backend | SQLite |
| `DJANGO_DB_NAME` | Database name or path | `db.sqlite3` |
| `DJANGO_DB_USER` | Database user | Empty |
| `DJANGO_DB_PASSWORD` | Database password | Empty |
| `DJANGO_DB_HOST` | Database host | Empty |
| `DJANGO_DB_PORT` | Database port | Empty |

Production uses `config.settings.production`. It rejects the development secret key and an
empty allowed-host list, enables secure cookies, HTTPS redirects, HSTS, and defensive HTTP
headers. Set `DJANGO_SECURE_SSL_REDIRECT=false` only when TLS termination and proxy handling
make that appropriate for your deployment.

## Development commands

```bash
make check       # lint, formatting, types, Django checks, and migration drift
make test        # tests with branch coverage (minimum 90%)
make docs        # regenerate the reference sections of docs/
make package     # build and validate wheel and source distribution
make migrations  # create migrations
make migrate     # apply migrations
make superuser   # create an admin user
make run         # start the development server (WSGI: no WebSocket)
make serve       # start an ASGI server, which does serve the WebSocket
make example     # build the example project and tour every app in it
```

## Package maintenance and publishing

The Python package lives in `src/django_ninja_starter/`; its bundled project scaffold lives
in `src/django_ninja_starter/template/`. Update the runnable root starter and the bundled
scaffold together when changing project behavior.

To publish a release:

1. Update `version` in `pyproject.toml` and `__version__` in
   `src/django_ninja_starter/__init__.py`.
2. Run `make check`, `make test`, and `make package`.
3. Configure a PyPI Trusted Publisher for `.github/workflows/release.yml` with the `pypi`
   GitHub environment and require approval on that environment.
4. Create and publish a GitHub Release. The release workflow builds and uploads the wheel
   and source distribution without a long-lived PyPI token.

## License

MIT. See [LICENSE](LICENSE).
