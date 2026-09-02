.PHONY: install check test docs example package run serve migrate migrations superuser

install:
	python3 -m pip install -e '.[dev]'

check:
	ruff check --no-cache .
	ruff format --check --no-cache .
	mypy src manage.py examples/build.py
	python3 manage.py check
	python3 manage.py makemigrations --check --dry-run

test:
	pytest --cov

example:
	python3 examples/walkthrough.py --rebuild

docs:
	DJANGO_SETTINGS_MODULE=config.settings.test python3 manage.py authdocs

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

migrate:
	python3 manage.py migrate

migrations:
	python3 manage.py makemigrations

superuser:
	python3 manage.py createsuperuser
