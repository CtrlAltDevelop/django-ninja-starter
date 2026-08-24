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

Open <http://127.0.0.1:8000/api/v1/docs> for interactive API documentation.

## Project layout

```text
src/
├── apps/                   # User-created business applications
├── infrastructure/
│   └── common/             # Project-owned foundation application
└── config/                 # Settings, URL routing, ASGI, and WSGI
```

Create business features under `src/apps/`. Keep project foundation code and technical
integrations under `src/infrastructure/`.

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
- `GET /api/v1/docs` — OpenAPI documentation
