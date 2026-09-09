# `apps.support`

Live chat and support tickets between a client and the desk, over four
transports at once: HTTP, a WebSocket, GraphQL and gRPC.

This directory is self-contained. It imports nothing from the project around it
except things it degrades gracefully without: `unfold` for the admin theme, and
`infrastructure.auth.core.sessions` for "which account does this bearer token
belong to?". Both are wrapped in `try/except ImportError` with a Django default
behind them, so the app can be copied into another project as it stands.

## Dropping it into another Django project

1. Copy this directory to `apps/support` (or anywhere importable) and add
   `apps.support.apps.SupportConfig` to `INSTALLED_APPS`.
2. Add the settings it reads. Every one has a default, so this is optional until
   you go to production:

   ```python
   SUPPORT_BROKER = "apps.support.broadcast.RedisBroker"
   SUPPORT_REDIS_URL = "redis://127.0.0.1:6379/0"
   SUPPORT_CHANNEL_PREFIX = "support"
   SUPPORT_WS_PATH = "/ws/support"
   SUPPORT_MAX_UPLOAD_BYTES = 10 * 1024 * 1024
   ```

3. Mount the router wherever your Django Ninja API is built:

   ```python
   api.add_router("/support", "apps.support.rest.v1.router")
   ```

4. Route the socket. `apps.support.sockets.support_socket` is a plain ASGI
   application, so it needs no Channels:

   ```python
   async def application(scope, receive, send):
       if scope["type"] == "websocket" and scope["path"] == "/ws/support":
           return await support_socket(scope, receive, send)
       return await django_application(scope, receive, send)
   ```

5. `manage.py migrate`, and serve it with an ASGI server — `runserver` is WSGI
   and will never open the socket.

## What it does

**A ticket is a conversation.** That one decision is why live chat and a formal
support ticket are one app rather than two that have to be kept in step: a
`Ticket` is a thread, a `Message` is something somebody said in it, and `kind`
says which of the two ways it is being used. A chat that turns out to be a real
problem is promoted by giving it a subject and a category, not by copying rows
into another table.

Two sides, and the difference between them is `is_staff`. A client sees their
own conversations and nobody else's; the desk sees the queue. Everything either
side may do is decided once, in `services.py`, so the four transports cannot
disagree about who may close a ticket or read a note.

**Internal notes live in the thread.** A message is `public` or `internal`, and
internal means staff-only. Both sit in the same thread in the order they were
written, because a thread whose notes are somewhere else is a thread nobody
reads in order. Every queryset a client can reach drops the internal ones, and
the socket drops them again on the way out.

**The SLA is stored as deadlines, never as a breach flag.** A flag needs
something to set it and is wrong for the whole window between the deadline
passing and that thing running. Two datetimes are written when the ticket is
created and compared against the clock when anybody asks.

**The socket can do everything the HTTP API can**, so a client holding one open
needs no HTTP client beside it — with one exception, and it is a protocol limit
rather than a choice: a file has to be uploaded over HTTP, because a WebSocket
frame is JSON and cannot carry a multipart body. The upload answers with an id
and the message that carries it goes over the socket like any other.

## Where things are

| File | What it holds |
| --- | --- |
| `models.py` | `Ticket`, `Message`, `Participant`, `Category`, `Tag`, `CannedReply`, `Upload`, `Attachment`, and the querysets that answer "what may this account see?" |
| `events.py` | The payloads every transport sends, and the publishing that puts them on a channel after commit |
| `broadcast.py` | Fan-out: the in-process broker and the Redis one |
| `sockets.py` | The WebSocket consumer, and the command table it dispatches on |
| `identity.py` | Turning a token, subprotocol, header or cookie into an account |
| `services.py` | Every question this app answers, decided once for all four transports |
| `uploads.py` | Storing a file and handing back the id a message claims it by |
| `rest/` | The HTTP endpoints and their schemas |
| `graph/` | The same surface as GraphQL queries and mutations |
| `grpc/` | The same, as gRPC actions, and the `.proto` they generate |
| `admin.py` | The desk's own screens: the queue, its SLA column, and the bulk actions |
