# Django Ninja Starter

A production-oriented Django and Django Ninja starter, available as both a GitHub
Template and an installable Python project generator.

It includes environment-specific settings, secure production defaults, health checks,
OpenAPI documentation, tests, typing, linting, coverage, CI, and a feature-first source
layout.

Authentication is included and opt-in: four login methods, four second factors, four
social providers and three token modes, each a separate app that installs nothing until
you name it. Every login ends by minting a signed JWT.

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

## Architecture

```text
src/
├── apps/                   # User-created business applications
├── infrastructure/
│   ├── common/             # Project-owned foundation application
│   ├── auth/               # Login methods and second factors
│   │   ├── core/           # Challenge store, delivery, throttling, credential issuance
│   │   ├── password/       # Username-or-email and password
│   │   ├── email_code/     # One-time code by email
│   │   ├── sms_code/       # One-time code by SMS
│   │   ├── magic_link/     # Single-use emailed link
│   │   └── twofactor/      # TOTP, SMS, email, and recovery second factors
│   ├── oauth_core/         # Shared clients, scopes, consent, PKCE, and audit models
│   ├── oauth_sliding/      # Sliding token mode
│   ├── oauth_session/      # Server-side session mode
│   └── oauth_rotation/     # Access/refresh rotation mode
└── config/
    ├── settings/           # Base, development, test, and production settings
    ├── api.py              # Versioned NinjaAPI composition root
    ├── urls.py
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
same records and share one revocation story. It defaults to `DJANGO_OAUTH_MODE` (and to
`rotation` when that is `all`); `none` signs in with a normal Django session instead. `check`
reports a mode whose app is not installed.

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

## Included endpoints

- `GET /api/v1/health/live` — confirms that the web process is serving requests
- `GET /api/v1/health/ready` — confirms that the database is available
- `GET /api/docs` — Swagger documentation with an API-version selector
- `GET /api/<version>/docs` — Swagger documentation opened on a specific version
- `GET /api/<version>/openapi.json` — OpenAPI schema for a specific version

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
make run          # start the development server
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
