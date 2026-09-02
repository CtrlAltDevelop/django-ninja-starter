# `apps.notifications`

Stored notifications, a read API, and a WebSocket that pushes new ones the
moment they are created — public before it is authenticated, private after.

This directory is self-contained. It imports nothing from the project around it
except two things it degrades gracefully without: `unfold` for the admin theme,
and `infrastructure.auth.core.sessions` for "which account does this bearer
token belong to?". Both are wrapped in `try/except ImportError` with a Django
default behind them, so the app can be copied into another project as it stands.

## Dropping it into another Django project

1. Copy this directory to `apps/notifications` (or anywhere importable) and add
   `apps.notifications.apps.NotificationsConfig` to `INSTALLED_APPS`.
2. Add the settings it reads. Every one of them has a default, so this is
   optional until you go to production:

   ```python
   NOTIFICATIONS_BROKER = "apps.notifications.broadcast.RedisBroker"
   NOTIFICATIONS_REDIS_URL = "redis://127.0.0.1:6379/0"
   NOTIFICATIONS_CHANNEL_PREFIX = "notifications"
   NOTIFICATIONS_SOCKET_BACKLOG = 20
   ```

3. Mount the router wherever your Django Ninja API is built:

   ```python
   api.add_router("/notifications", "apps.notifications.api.v1.router")
   ```

4. Route the socket. `apps.notifications.sockets.notifications_socket` is a
   plain ASGI application, so it needs no Channels:

   ```python
   async def application(scope, receive, send):
       if scope["type"] == "websocket" and scope["path"] == "/ws/notifications":
           return await notifications_socket(scope, receive, send)
       return await django_application(scope, receive, send)
   ```

5. `manage.py migrate`, and serve it with an ASGI server — `runserver` is WSGI
   and will never open the socket.

## Where things are

| File | What it holds |
| --- | --- |
| `models.py` | `Notification`, `NotificationReceipt`, and the queryset that answers "what can this account see, and has it read it?" |
| `events.py` | `notify_user`, `notify_everyone`, and the save signal that broadcasts |
| `broadcast.py` | Fan-out: the in-process broker and the Redis one |
| `sockets.py` | The WebSocket consumer |
| `identity.py` | Turning a token, subprotocol, header or cookie into an account |
| `api/v1.py` | The HTTP endpoints |
| `admin.py` | Writing one, and seeing who read it |
