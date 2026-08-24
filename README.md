# Django Ninja Starter

A production-oriented Django and Django Ninja starter, available as both a GitHub
Template and an installable Python project generator.

It includes environment-specific settings, secure production defaults, health checks,
OpenAPI documentation, tests, typing, linting, coverage, CI, and a feature-first source
layout.

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

`python manage.py check` reports enabled providers with missing credentials, invalid Apple callback
schemes, ambiguous Microsoft tenants, and unsafe timeout/state/clock-skew limits before deployment.

Upstream access and refresh tokens are not retained by default. Set
`DJANGO_OAUTH_STORE_PROVIDER_TOKENS=true` only when the application needs provider APIs. Stored
tokens and transient PKCE verifiers are encrypted with `DJANGO_OAUTH_ENCRYPTION_KEY`; when empty,
a key is derived from `DJANGO_SECRET_KEY`. Changing either key requires a credential migration or
provider reauthorization.

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
