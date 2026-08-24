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

Open <http://127.0.0.1:8000/api/v1/docs> for interactive API documentation.

## Architecture

```text
src/
├── apps/                   # User-created business applications
├── infrastructure/
│   └── common/             # Project-owned foundation application
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

## Included endpoints

- `GET /api/v1/health/live` — confirms that the web process is serving requests
- `GET /api/v1/health/ready` — confirms that the database is available
- `GET /api/v1/docs` — interactive OpenAPI documentation

## Configuration

Development uses SQLite by default. Copy `.env.example` to `.env` and configure these
variables as needed:

| Variable | Purpose | Default |
| --- | --- | --- |
| `DJANGO_SETTINGS_MODULE` | Active settings module | `config.settings.development` |
| `DJANGO_SECRET_KEY` | Django signing key | Unsafe development value |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames | `localhost,127.0.0.1` in `.env.example` |
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
