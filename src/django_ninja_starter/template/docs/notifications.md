# Notifications

Stored notifications, a read API, and a WebSocket that pushes new ones the
moment they are created. Optional in the same way every login method is: naming
it in `DJANGO_NOTIFICATIONS_ENABLED` is what installs it, and a project that does
not name it carries no notification tables, no routes, no socket and never
imports the package.

Like the [CMS](cms.md), it is a **feature app**: it lives in
`src/apps/notifications`, and the directory can be copied into another Django
project or deleted from this one, and neither leaves a hole. Its own
[`README`](../src/apps/notifications/README.md) is the drop-it-in-elsewhere
guide.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/notifications` | Bearer | List every notification this account can see |
| `POST` | `/api/v1/notifications/read-all` | Bearer | Mark everything read |
| `GET` | `/api/v1/notifications/unread-count` | Bearer | Count what is still unread |
| `POST` | `/api/v1/notifications/{notification_id}/read` | Bearer | Mark one notification read |
<!-- /generated:routes -->

Every one of them is scoped to the caller: a global notification is visible to
everybody, a directed one only to its recipient, and read state is per account.

## Running the socket

It is a plain ASGI application — no Channels — mounted at
`DJANGO_NOTIFICATIONS_WS_PATH` (`/ws/notifications` by default) by
[`config/sockets.py`](../src/config/sockets.py), which is where `config/asgi.py`
sends every `websocket` scope. A path with nothing mounted on it is closed with
code `4404` rather than left hanging.

`manage.py runserver` is WSGI and will never serve it — the connection simply
never opens, which is a confusing way to find out. Serve it with an ASGI server:

```bash
make serve
```

That is `uvicorn config.asgi:application --reload --app-dir src` with the
development settings, since `config/asgi.py` defaults to production settings the
way a deployment expects. It needs the `asgi` extra, which `dev` already pulls
in:

```bash
pip install -e '.[asgi]'
```

The extra is uvicorn *and* a WebSocket implementation, because uvicorn on its own
has none and quietly falls back to speaking only HTTP. The upgrade request is
then handled as an ordinary `GET`, which Django answers with **404 Not Found** —
so a missing dependency looks exactly like a socket that was never mounted.

## The socket protocol

**The connection is useful before it is authenticated.** Anyone who connects
joins the global channel and starts receiving what was addressed to everybody —
a status banner, a maintenance notice — with no credential at all. Sending
`authenticate` adds that account's own channel to the *same* connection, so a
client opens one socket rather than one per audience, and a page that renders
announcements to signed-out visitors needs no special case.

A credential presented in the handshake is honoured too, so a client that
already knows who it is need not wait a round trip. Four places are read, in
this order:

| Where | Looks like | Who uses it |
| --- | --- | --- |
| Query string | `?token=…` | A browser: `WebSocket` cannot set headers |
| Subprotocol | `Sec-WebSocket-Protocol: bearer, …` | A browser that would rather not put a token in a URL |
| Header | `Authorization: Bearer …` | A native or server-side client |
| Cookie | `sessionid=…` | A browser signed in through Django's own login |

A subprotocol that is offered is echoed on accept, because a browser that
offered one and is answered with none closes the connection itself. A handshake
credential that identifies nobody is *not* a refusal: the connection opens
anonymously, since denying the public feed over a bad private credential helps
no one.

**Any command may carry the token, not only `authenticate`.** A client holding a
credential can make `{"command": "unread", "token": "…"}` its first frame: it is
signed in exactly as `authenticate` would have signed it in — same
`authenticated` frame, same backlog — and then the command runs. The token is
checked *before* the command, so a refusal never half-happens, and a token
naming a different account conflicts here as it would there. Sending it on a
connection already signed in as that account is honoured silently: there is
nothing to announce, and the client asked a question rather than for an
acknowledgement.

### What the client sends

| Command | Answered with | Notes |
| --- | --- | --- |
| `{"command": "authenticate", "token": "…"}` | `authenticated`, then the unread backlog | Again with the same account is answered rather than ignored, so a refreshed token needs no reconnect |
| `{"command": "read", "id": "…"}` | `read` | Needs an account |
| `{"command": "read_all"}` | `read_all` | Needs an account |
| `{"command": "unread"}` | `unread` | Needs an account |
| `{"command": "ping"}` | `pong` | For holding an idle connection open through a proxy |

Every one of them also accepts a `token`, which signs the connection in before
the command runs. An absent, null or empty one is not a failed credential: the
command meets whatever answer it would have met on its own.

### What the server sends

Every frame is a JSON object with a `type`.

| Frame | When | Payload |
| --- | --- | --- |
| `ready` | Once, on connect | `authenticated`, `user`, `unread` |
| `authenticated` | After a successful `authenticate`, or the first command to carry a token | `user`, `unread` |
| `notification` | A notification was created for a channel this connection is on, and on catch-up | `notification` |
| `read` | After `read` | `id`, `unread` |
| `read_all` | After `read_all` | `count`, `unread` |
| `unread` | After `unread` | `count` |
| `pong` | After `ping` | — |
| `error` | Any refusal | `title`, `description` |

A notification frame carries the same object the [list endpoint](#routes)
returns:

```json
{
  "type": "notification",
  "notification": {
    "id": "09fa89ef-87e5-4ebf-a8c2-0a08283c478e",
    "audience": "global",
    "subject": "Maintenance at 22:00 UTC",
    "body": "",
    "level": "warning",
    "link": "",
    "data": {},
    "created_at": "2026-09-02T07:20:03.983980+00:00",
    "read": false
  }
}
```

### Errors

Errors are frames rather than closes: a mistyped notification id should cost one
message, not the connection and everything else flowing over it. Their titles
are the same vocabulary [the HTTP envelope](responses.md) uses, so a client
translates one set of strings.

| Title | Raised by |
| --- | --- |
| `TOKEN_INVALID` | A token that identifies nobody, on `authenticate` or on any command carrying one |
| `CONFLICT` | A token for a *different* account on a connection already signed in — there is no honest way to serve two people down one socket, so open a second |
| `AUTHENTICATION_REQUIRED` | `read`, `read_all` or `unread` before authenticating |
| `NOT_FOUND` | A notification id that does not exist, or belongs to somebody else — the same answer for both, since saying which would confirm another account's mail |
| `BAD_REQUEST` | A binary frame, a body that is not a JSON object, an unknown command, or an id that is not a UUID |

## Models

<!-- generated:models -->
#### `Notification`

One thing worth telling somebody about.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `audience` | Char |  |
| `recipient` | ForeignKey | → `accounts.User`, nullable |
| `subject` | Char |  |
| `body` | Text |  |
| `level` | Char |  |
| `link` | Char |  |
| `data` | JSON |  |
| `created_at` | DateTime | not editable |

#### `NotificationReceipt`

One account having read one notification. Its existence is the read state.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | BigAuto | primary key |
| `notification` | ForeignKey | → `notifications.Notification` |
| `user` | ForeignKey | → `accounts.User` |
| `read_at` | DateTime |  |
<!-- /generated:models -->

Two rows, because there are two questions with different cardinality. A
**notification** is the thing that happened, addressed either to one account or
to everybody — a stored fact rather than "recipient happens to be null", since a
broadcast and a message whose recipient was deleted are not the same thing. A
**receipt** is one account having read one notification: read state cannot live
on the notification, because a global one is read by each person separately.
Unread is therefore the same query shape for both audiences instead of
broadcasts being the case every caller forgets.

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `Notification` | Yes | — | `subject`, `audience_label`, `recipient`, `level_badge`, `read_by`, `created_at` |
| `NotificationReceipt` | No — read-only | — | `notification`, `user`, `read_at` |
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_NOTIFICATIONS_BROKER` | Recommended | how a notification created in one process reaches sockets held by another. |
| `DJANGO_NOTIFICATIONS_SOCKET_BACKLOG` | Optional | how many unread notifications a client is caught up with on connect. Range 0–500. |
<!-- /generated:settings -->

```bash
DJANGO_NOTIFICATIONS_ENABLED=true
DJANGO_NOTIFICATIONS_BROKER=apps.notifications.broadcast.RedisBroker   # in production
DJANGO_NOTIFICATIONS_REDIS_URL=redis://127.0.0.1:6379/0               # defaults to DJANGO_AUTH_REDIS_URL
DJANGO_NOTIFICATIONS_WS_PATH=/ws/notifications                        # tell your proxy the same
DJANGO_NOTIFICATIONS_CHANNEL_PREFIX=notifications                     # namespaces the Redis channels
DJANGO_NOTIFICATIONS_SOCKET_BACKLOG=20
```

Only the first line is needed to start. `RedisBroker` needs the `redis` extra,
which `dev` already pulls in.

The **broker** is the only genuinely hard part, and the setting worth reading
twice. A notification is created in ordinary synchronous request code and has to
reach sockets held open somewhere else, possibly in another worker. The default
`MemoryBroker` fans out inside one process — it needs nothing installed, which is
what lets the app work the moment it is enabled, and under two workers a client
connected to the first never hears about a notification created by the second.
`RedisBroker` is the same thing across a deployment. `manage.py check` warns
while the default is still in place rather than leaving it to be discovered.

## Using it

Creating a notification is a function call, and the socket push happens on save:

```python
from apps.notifications.events import notify_everyone, notify_user

notify_user(user, subject="Your export is ready", link="/exports/42")
notify_everyone(subject="Maintenance at 22:00 UTC", level="warning")
```

Anything that saves a `Notification` — the admin, a management command, a
fixture — publishes it too, because the broadcast hangs off the save signal
rather than off those helpers.

Note that `on_commit` is what publishes, so a notification created inside a
transaction that later rolls back is never pushed — a client told about a
notification it can never fetch is worse than one told a moment later.

On the other side, the whole client is this:

```js
const socket = new WebSocket("wss://example.com/ws/notifications");

socket.onmessage = (message) => {
  const frame = JSON.parse(message.data);
  if (frame.type === "notification") render(frame.notification);
  if (frame.type === "ready" && !frame.authenticated) {
    socket.send(JSON.stringify({ command: "authenticate", token: accessToken }));
  }
};
```

Opening it before sign-in and authenticating afterwards is the intended shape: no
reconnect, and announcements render for a visitor who never signs in at all.

Reading it back over HTTP is the fallback for a client that was not connected:
the socket catches a reconnecting client up with its unread backlog
(`DJANGO_NOTIFICATIONS_SOCKET_BACKLOG`, twenty by default), and the list
endpoint holds the rest of the history.
