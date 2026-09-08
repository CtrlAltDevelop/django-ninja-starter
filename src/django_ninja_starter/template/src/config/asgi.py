"""The ASGI entry point: Django for HTTP, this project's own router for WebSockets.

``get_asgi_application`` handles the ``http`` scope and nothing else -- handed a
``websocket`` scope it raises, which is why the two protocols are separated here
rather than inside Django. The dispatch is small enough to read, and keeps
Channels out of a project that needs one socket.

Note that ``manage.py runserver`` is WSGI and will never serve the WebSocket. In
development, run this module under an ASGI server instead::

    uvicorn config.asgi:application --reload
"""

import os
from typing import Any

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")

django_application = get_asgi_application()


async def application(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] == "websocket":
        from config.sockets import websocket_application

        await websocket_application(scope, receive, send)
        return
    await django_application(scope, receive, send)
