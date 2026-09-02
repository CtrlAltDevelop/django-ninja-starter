.PHONY: install check test run serve migrate migrations superuser

install:
	python3 -m pip install -e '.[dev]'

check:
	ruff check --no-cache .
	ruff format --check --no-cache .
	mypy src manage.py
	python3 manage.py check
	python3 manage.py makemigrations --check --dry-run

test:
	pytest --cov

docs:
	DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py authdocs

run:
	python3 manage.py runserver

# `runserver` is WSGI and will not serve the notification WebSocket; this will.
# `config/asgi.py` defaults to production settings, the way a deployment expects.
serve:
	DJANGO_SETTINGS_MODULE=config.settings.development \
		python3 -m uvicorn config.asgi:application --reload --app-dir src

migrate:
	python3 manage.py migrate

migrations:
	python3 manage.py makemigrations

superuser:
	python3 manage.py createsuperuser
