.PHONY: install check test protos run serve grpc migrate migrations superuser

install:
	python3 -m pip install -e '.[dev]'

# Every optional app turned on, so the checks below see the whole project
# rather than whatever this developer happens to have in their `.env`. An app
# left off is an app whose models, admin, migrations and protos nobody checked.
ALL_APPS = DJANGO_CMS_ENABLED=true DJANGO_NOTIFICATIONS_ENABLED=true DJANGO_SHOP_ENABLED=true DJANGO_SUPPORT_ENABLED=true DJANGO_WALLET_ENABLED=true

check:
	ruff check --no-cache .
	ruff format --check --no-cache .
	mypy src manage.py
	$(ALL_APPS) python3 manage.py check
	$(ALL_APPS) python3 manage.py makemigrations --check --dry-run
	$(ALL_APPS) python3 manage.py protos --check

test:
	pytest --cov

docs:
	DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py authdocs

# Rewrite every app's .proto and its Python stubs from the gRPC services.
# Run it after changing a `@grpc_action`, and commit what it writes.
protos:
	python3 manage.py protos

run:
	python3 manage.py runserver

# `runserver` is WSGI and will not serve the WebSockets; this will, and it serves
# the static files too -- see `config/asgi.py`, which puts in the handler
# `runserver` would have added.
#
# `--reload-include` because uvicorn's reloader watches Python files only: a
# changed template would otherwise go on serving its old self until somebody
# restarted by hand, which is a confusing half hour to spend.
#
# `config/asgi.py` defaults to production settings, the way a deployment expects.
serve:
	DJANGO_SETTINGS_MODULE=config.settings.development \
		python3 -m uvicorn config.asgi:application --reload --app-dir src \
		--reload-include '*.html' --reload-include '*.js' --reload-include '*.css'

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
