.PHONY: install check test run migrate migrations superuser

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

migrate:
	python3 manage.py migrate

migrations:
	python3 manage.py makemigrations

superuser:
	python3 manage.py createsuperuser
