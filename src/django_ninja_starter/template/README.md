# {{ project_title }}

A production-oriented API built with Django and Django Ninja.

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
├── apps/                   # User-created business applications
├── infrastructure/
│   ├── common/             # Project-owned foundation application
│   ├── oauth_core/         # Shared clients, scopes, consent, PKCE, and audit models
│   ├── oauth_sliding/      # Sliding token mode
│   ├── oauth_session/      # Server-side session mode
│   └── oauth_rotation/     # Access/refresh rotation mode
└── config/                 # Settings, URL routing, ASGI, and WSGI
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
timeouts are built in. Upstream tokens are discarded unless
`DJANGO_OAUTH_STORE_PROVIDER_TOKENS=true`; when retained, they are encrypted using
`DJANGO_OAUTH_ENCRYPTION_KEY` or a key derived from `DJANGO_SECRET_KEY`.

Microsoft tenant configuration accepts `common`, `organizations`, `consumers`, or a tenant GUID.
Run `python manage.py check` to catch missing credentials, invalid callback schemes, and unsafe
OAuth timeout, state-lifetime, or clock-skew settings before deployment.

## Commands

```bash
make check       # lint, format, types, Django checks, and migration drift
make test        # tests with branch coverage
make migrations  # create migrations
make migrate     # apply migrations
make superuser   # create an admin user
make run          # start the development server
```

## Configuration

Development uses SQLite by default. Copy `.env.example` to `.env` and set the `DJANGO_*`
variables for another database or production deployment. Production uses
`config.settings.production` and requires a secure `DJANGO_SECRET_KEY` and non-empty
`DJANGO_ALLOWED_HOSTS`.

## Included endpoints

- `GET /api/v1/health/live` — process liveness
- `GET /api/v1/health/ready` — database readiness
- `GET /api/docs` — Swagger documentation with an API-version selector
- `GET /api/<version>/openapi.json` — version-specific OpenAPI schema
