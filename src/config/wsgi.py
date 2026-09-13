import os

from django.core.wsgi import get_wsgi_application

from config.preflight import verify_configuration

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

application = get_wsgi_application()

# `get_wsgi_application` runs `django.setup()`, so the app registry the checks
# walk is populated by the time this runs -- and nothing has been served yet.
verify_configuration()
