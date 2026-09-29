"""The ASGI entry point: Django for HTTP, this project's own router for WebSockets.

``get_asgi_application`` handles the ``http`` scope and nothing else -- handed a
``websocket`` scope it raises, which is why the two protocols are separated here
rather than inside Django. The dispatch is small enough to read, and keeps
Channels out of a project that needs one socket.

Note that ``manage.py runserver`` is WSGI and will never serve the WebSocket. In
development, run this module under an ASGI server instead::

    uvicorn config.asgi:application --reload

**Static files are served here while ``DEBUG`` is on, and nowhere else.**
``runserver`` quietly swaps in a handler that serves ``STATIC_URL`` from the
finders; an ASGI server does not, so a project served this way loads its admin
with no stylesheet and no explanation -- the pages answer 200 and the CSS
answers 404. The same handler is put in by hand below, under the same condition
Django uses, so development serves its own assets and production goes on serving
them from a web server or an object store with `collectstatic`.
"""

import os
from typing import Any

from django.core.asgi import get_asgi_application

from config.preflight import verify_configuration

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")


def _debug() -> bool:
    """Whether this is a machine somebody is developing on.

    The same signal the admin's environment badge uses, and the same one
    ``config/urls.py`` serves MEDIA_URL under: one line between a development
    machine and a deployment, rather than a second setting to keep in step.
    """
    from django.conf import settings

    return bool(settings.DEBUG)


django_application: Any = get_asgi_application()

# Only while DEBUG is on, and imported here rather than at module scope because
# settings are not configured until `get_asgi_application` has run.
if _debug():
    from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

    django_application = ASGIStaticFilesHandler(django_application)

# After the application is built, because that is what runs `django.setup()`
# and populates the app registry the checks walk; before a single request is
# served, because that is the point.
verify_configuration()


async def application(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] == "websocket":
        from config.sockets import websocket_application

        await websocket_application(scope, receive, send)
        return
    await django_application(scope, receive, send)
