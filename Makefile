.PHONY: install check test docs example package protos run serve grpc migrate migrations superuser

install:
	python3 -m pip install -e '.[dev]'

# Every optional app turned on, so the checks below see the whole project
# rather than whatever this developer happens to have in their `.env`. An app
# left off is an app whose models, admin, migrations and protos nobody checked.
ALL_APPS = DJANGO_CMS_ENABLED=true DJANGO_NOTIFICATIONS_ENABLED=true DJANGO_SHOP_ENABLED=true DJANGO_SUPPORT_ENABLED=true

check:
	ruff check --no-cache .
	ruff format --check --no-cache .
	mypy src manage.py examples/build.py
	$(ALL_APPS) python3 manage.py check
	$(ALL_APPS) python3 manage.py makemigrations --check --dry-run
	$(ALL_APPS) python3 manage.py protos --check

test:
	pytest --cov

example:
	python3 examples/walkthrough.py --rebuild

docs:
	DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py authdocs

# Rewrite every app's .proto and its Python stubs from the gRPC services.
# Run it after changing a `@grpc_action`, and commit what it writes.
protos:
	python3 manage.py protos

package:
	python3 -m build
	python3 -m twine check dist/*

run:
	python3 manage.py runserver

# `runserver` is WSGI and will not serve the notification WebSocket; this will.
# `config/asgi.py` defaults to production settings, the way a deployment expects.
serve:
	DJANGO_SETTINGS_MODULE=config.settings.development \
		python3 -m uvicorn config.asgi:application --reload --app-dir src

# The gRPC server. `runserver` serves REST and GraphQL; this serves the third
# door, on DJANGO_GRPC_PORT.
grpc:
	python3 manage.py grpcrunaioserver

migrate:
	python3 manage.py migrate

migrations:
	python3 manage.py makemigrations

superuser:
	python3 manage.py createsuperuser
