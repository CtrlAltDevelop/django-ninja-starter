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
| `POST` | `/api/v1/notifications/dismiss-all` | Bearer | Empty the tray |
| `POST` | `/api/v1/notifications/read-all` | Bearer | Mark everything read |
| `GET` | `/api/v1/notifications/unread-count` | Bearer | Count what is still unread |
| `GET` | `/api/v1/notifications/{notification_id}` | Bearer | Read one notification |
| `POST` | `/api/v1/notifications/{notification_id}/dismiss` | Bearer | Take one out of the tray |
| `POST` | `/api/v1/notifications/{notification_id}/read` | Bearer | Mark one notification read |
| `POST` | `/api/v1/notifications/{notification_id}/restore` | Bearer | Put a dismissed one back |
| `POST` | `/api/v1/notifications/{notification_id}/unread` | Bearer | Mark one notification unread |
<!-- /generated:routes -->

Every one of them is scoped to the caller: a global notification is visible to
everybody, a directed one only to its recipient, and read state is per account.

The same surface is published four ways — these endpoints, the
[socket](#the-socket-protocol), GraphQL and gRPC — under the same names, with
the same arguments and the same replies. Nothing in any of them creates a
notification: [the code with something to say](#using-it) does that.

### An API walkthrough

Every call carries the same bearer token the rest of the API takes, and every
response is wrapped in [the standard envelope](responses.md) — `data` below is
that envelope's payload.

```bash
TOKEN=...   # from signing in; see docs/signing-in.md
API=https://example.com/api/v1/notifications
AUTH="Authorization: Bearer $TOKEN"
```

**Open the tray.** Newest first, with the total so you can page:

```bash
curl -H "$AUTH" "$API?limit=20"
```

```json
{"data": {"notifications": [{"id": "09fa89ef-…", "subject": "Your export is ready",
  "level": "info", "link": "/exports/1", "data": {}, "audience": "user",
  "created_at": "2026-09-02T07:20:03.983980+00:00", "read": false, "dismissed": false}],
  "total": 47, "limit": 20, "offset": 0}}
```

**Narrow it.** `unread` is three-valued — leaving it out means "everything",
which is not the same question as `unread=false`:

```bash
curl -H "$AUTH" "$API?unread=true"                  # the tray on open
curl -H "$AUTH" "$API?level=error"                  # an errors-only view
curl -H "$AUTH" "$API?audience=global"              # announcements alone
curl -H "$AUTH" "$API?include_dismissed=true"       # including what was cleared away
curl -H "$AUTH" "$API?limit=20&offset=20"           # the next page
```

An unknown `level` or `audience` is a **422**, not an empty list: the schema
knows the choices, so a typo is answerable rather than silent.

**Just the badge**, without the payload of the list it counts:

```bash
curl -H "$AUTH" "$API/unread-count"        # {"data": {"count": 3}}
```

**One notification**, dismissed or not — a link to something cleared away should
still open it:

```bash
curl -H "$AUTH" "$API/09fa89ef-87e5-4ebf-a8c2-0a08283c478e"
```

**Change what one of them is.** All four answer the same shape:

```bash
curl -X POST -H "$AUTH" "$API/09fa89ef-…/read"      # {"data": {"id": "09fa89ef-…", "unread": 2, "changed": true}}
curl -X POST -H "$AUTH" "$API/09fa89ef-…/unread"    # undo it
curl -X POST -H "$AUTH" "$API/09fa89ef-…/dismiss"   # out of the tray, marked read on the way
curl -X POST -H "$AUTH" "$API/09fa89ef-…/restore"   # back into it, read state untouched
```

`changed` is false when it was already in that state. Still a 200: marking a
read notification read is not an error.

**Everything at once:**

```bash
curl -X POST -H "$AUTH" "$API/read-all"      # {"data": {"count": 12, "unread": 0}}
curl -X POST -H "$AUTH" "$API/dismiss-all"   # empty the tray, marking it all read
```

**Refusals.** A notification that does not exist and one addressed to somebody
else are both a **404** — a 403 would confirm that the other account's mail
exists, which is the leak. Every endpoint is **401** without a credential.

Dismissing never deletes. A broadcast belongs to everybody, so one person
clearing an announcement leaves it in everyone else's tray; what is stored is a
per-account receipt, not a removal.

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

**The socket can do everything the HTTP API can.** A client holding one open
needs no HTTP client beside it: `list`, `get` and `count` read the history the
socket itself never pushes, and the six change commands do what the endpoints
do. That is deliberate — a tray built on the socket should not have to keep a
second transport around just to render its first page.

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

Four commands answer with no account at all — they are the connection's own
housekeeping, not anybody's mail:

| Command | Answered with | Notes |
| --- | --- | --- |
| `{"command": "ping"}` | `pong` | For holding an idle connection open through a proxy |
| `{"command": "authenticate", "token": "…"}` | `authenticated`, then the unread backlog | Again with the same account is answered rather than ignored, so a refreshed token needs no reconnect |
| `{"command": "whoami"}` | `whoami` | Answered, not refused, when nobody is signed in — "you are nobody" is the useful reply to a client that has just woken up |
| `{"command": "deauthenticate"}` | `deauthenticated` | Stops the private traffic and keeps the public feed and the connection |

Everything else is refused with `AUTHENTICATION_REQUIRED` until a credential
arrives, and answers once one has:

| Command | Answered with | Arguments |
| --- | --- | --- |
| `{"command": "list"}` | `list` | `unread`, `level`, `audience`, `include_dismissed`, `limit`, `offset` — the endpoint's, under the same names |
| `{"command": "get", "id": "…"}` | `notification` | Dismissed ones included |
| `{"command": "count"}` | `count` | The same filters as `list`, without the rows |
| `{"command": "unread"}` | `unread` | The badge number |
| `{"command": "read", "id": "…"}` | `read` | |
| `{"command": "unread_one", "id": "…"}` | `unread_one` | Undo a read. Named apart from `unread`, which is the badge |
| `{"command": "read_all"}` | `read_all` | |
| `{"command": "dismiss", "id": "…"}` | `dismiss` | Out of this account's tray, and marked read on the way |
| `{"command": "restore", "id": "…"}` | `restore` | Back into the tray; read state left alone |
| `{"command": "dismiss_all"}` | `dismiss_all` | Empty the tray |

Every one of them — the public four included — also accepts a `token`, which
signs the connection in before the command runs. An absent, null or empty one is
not a failed credential: the command meets whatever answer it would have met on
its own.

Arguments are checked rather than coerced: `{"limit": "all"}` is a
`BAD_REQUEST`, not a quiet fall back to the default page.

### What the server sends

Every frame is a JSON object with a `type`.

| Frame | When | Payload |
| --- | --- | --- |
| `ready` | Once, on connect | `authenticated`, `user`, `unread` |
| `authenticated` | After a successful `authenticate`, or the first command to carry a token | `user`, `unread` |
| `deauthenticated` | After `deauthenticate` | `user` — whoever just left, or null |
| `whoami` | After `whoami` | `authenticated`, `user`, `unread` |
| `notification` | A notification was created for a channel this connection is on, on catch-up, and in answer to `get` | `notification` |
| `list` | After `list` | `notifications`, `total`, `limit`, `offset` |
| `count` | After `count` | `count` |
| `unread` | After `unread` | `count` |
| `read` / `unread_one` / `dismiss` / `restore` | After the command of that name | `id`, `unread`, `changed` |
| `read_all` / `dismiss_all` | After the command of that name | `count`, `unread` |
| `state` | Something was read or dismissed on **another** connection of this account | `action`, `ids`, `unread` |
| `pong` | After `ping` | — |
| `error` | Any refusal | `title`, `description` |

`changed` is false when the notification was already in that state. That is
still success: marking a read notification read is not an error, and a client
that fired twice should not have to care which arrived first.

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
    "read": false,
    "dismissed": false
  }
}
```

### Two devices, one account

Every state change is also pushed to the account's *own* channel, which all of
its connections are on:

```json
{"type": "state", "action": "read", "ids": ["09fa89ef-…"], "unread": 3}
```

So a badge cleared on a phone clears on the laptop, without either client
polling. `action` is the command that caused it — `read`, `unread`, `dismiss`,
`restore`, `read_all`, `dismiss_all` — and `ids` is empty for the two that
change everything.

The connection that asked for the change receives its direct reply **and** this
frame. That is redundant rather than wrong: both say the same thing, applying
either twice is a no-op, and excluding one connection is not something the
broker can express. A client that wants to ignore its own echo can, but does not
need to.

A change that changed nothing publishes nothing, so a client polling `read` does
not flood every other device.

### Errors

Errors are frames rather than closes: a mistyped notification id should cost one
message, not the connection and everything else flowing over it. Their titles
are the same vocabulary [the HTTP envelope](responses.md) uses, so a client
translates one set of strings.

| Title | Raised by |
| --- | --- |
| `TOKEN_INVALID` | A token that identifies nobody, on `authenticate` or on any command carrying one |
| `CONFLICT` | A token for a *different* account on a connection already signed in — there is no honest way to serve two people down one socket, so open a second |
| `AUTHENTICATION_REQUIRED` | Any command outside the public four, before authenticating |
| `NOT_FOUND` | A notification id that does not exist, or belongs to somebody else — the same answer for both, since saying which would confirm another account's mail |
| `BAD_REQUEST` | A binary frame, a body that is not a JSON object, an unknown command, an id that is not a UUID, or an argument of the wrong type |

### A walkthrough

One connection, from a signed-out visitor to a signed-in tray and back. `→` is
what the client sends, `←` what the server sends.

```jsonc
// Connect with no credential at all.
←  {"type": "ready", "authenticated": false, "user": null, "unread": 0}

// Announcements arrive already. Nothing was presented, nothing is private.
←  {"type": "notification", "notification": {"audience": "global", "subject": "Maintenance at 22:00 UTC", …}}

// The visitor signs in. The same socket, not a new one.
→  {"command": "authenticate", "token": "eyJhbGciOi…"}
←  {"type": "authenticated", "user": {"id": "…", "username": "alice", "email": "…"}, "unread": 2}
←  {"type": "notification", "notification": {"subject": "Your export is ready", …}}   // the backlog,
←  {"type": "notification", "notification": {"subject": "Maintenance at 22:00 UTC", …}} // oldest first

// The tray opens and wants more than the backlog.
→  {"command": "list", "limit": 20}
←  {"type": "list", "notifications": [ … ], "total": 47, "limit": 20, "offset": 0}

// Only the errors, please.
→  {"command": "list", "level": "error", "unread": true}
←  {"type": "list", "notifications": [ … ], "total": 3, "limit": 50, "offset": 0}

// Somebody clicks one.
→  {"command": "read", "id": "09fa89ef-87e5-4ebf-a8c2-0a08283c478e"}
←  {"type": "read", "id": "09fa89ef-…", "unread": 1, "changed": true}
←  {"type": "state", "action": "read", "ids": ["09fa89ef-…"], "unread": 1}   // and so does the laptop

// Clicked again — success, and nothing moved.
→  {"command": "read", "id": "09fa89ef-87e5-4ebf-a8c2-0a08283c478e"}
←  {"type": "read", "id": "09fa89ef-…", "unread": 1, "changed": false}

// Misclick.
→  {"command": "unread_one", "id": "09fa89ef-87e5-4ebf-a8c2-0a08283c478e"}
←  {"type": "unread_one", "id": "09fa89ef-…", "unread": 2, "changed": true}

// Swiped away. Read on the way out, so the badge does not lie.
→  {"command": "dismiss", "id": "09fa89ef-87e5-4ebf-a8c2-0a08283c478e"}
←  {"type": "dismiss", "id": "09fa89ef-…", "unread": 1, "changed": true}

// Undo.
→  {"command": "restore", "id": "09fa89ef-87e5-4ebf-a8c2-0a08283c478e"}
←  {"type": "restore", "id": "09fa89ef-…", "unread": 1, "changed": true}

// "Clear all".
→  {"command": "dismiss_all"}
←  {"type": "dismiss_all", "count": 12, "unread": 0}

// A mistyped id costs one frame, not the connection.
→  {"command": "read", "id": "banana"}
←  {"type": "error", "title": "BAD_REQUEST", "description": "That is not a notification id."}

// Keeping an idle connection open through a proxy.
→  {"command": "ping"}
←  {"type": "pong"}

// Signing out. The socket stays; the announcements keep coming.
→  {"command": "deauthenticate"}
←  {"type": "deauthenticated", "user": {"username": "alice", …}}
←  {"type": "notification", "notification": {"audience": "global", …}}
→  {"command": "unread"}
←  {"type": "error", "title": "AUTHENTICATION_REQUIRED", "description": "Send an authenticate command before reading your own notifications."}
```

A client that already has a credential can skip the first exchange entirely by
putting it on the handshake, or by riding it on its first real command:

```jsonc
→  {"command": "list", "token": "eyJhbGciOi…"}
←  {"type": "authenticated", "user": { … }, "unread": 2}   // the sign-in it implied,
←  {"type": "notification", "notification": { … }}          // its backlog,
←  {"type": "list", "notifications": [ … ], "total": 47, "limit": 50, "offset": 0}   // then the answer
```

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

One account's state for one notification: when it was read, when dismissed.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | BigAuto | primary key |
| `notification` | ForeignKey | → `notifications.Notification` |
| `user` | ForeignKey | → `accounts.User` |
| `read_at` | DateTime | nullable |
| `dismissed_at` | DateTime | nullable |
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
| `NotificationReceipt` | No — read-only | — | `notification`, `user`, `read_at`, `dismissed_at` |
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_NOTIFICATIONS_BROKER` | Recommended | how a notification created in one process reaches sockets held by another. |
| `DJANGO_NOTIFICATIONS_RETENTION_DAYS` | Optional | how long notifications are kept before `manage.py notifications_prune` deletes them. 0 or more. |
| `DJANGO_NOTIFICATIONS_SOCKET_BACKLOG` | Optional | how many unread notifications a client is caught up with on connect. Range 0–500. |
<!-- /generated:settings -->

```bash
DJANGO_NOTIFICATIONS_ENABLED=true
DJANGO_NOTIFICATIONS_BROKER=apps.notifications.broadcast.RedisBroker   # in production
DJANGO_NOTIFICATIONS_REDIS_URL=redis://127.0.0.1:6379/0               # defaults to DJANGO_AUTH_REDIS_URL
DJANGO_NOTIFICATIONS_WS_PATH=/ws/notifications                        # tell your proxy the same
DJANGO_NOTIFICATIONS_CHANNEL_PREFIX=notifications                     # namespaces the Redis channels
DJANGO_NOTIFICATIONS_SOCKET_BACKLOG=20
DJANGO_NOTIFICATIONS_RETENTION_DAYS=0                                  # 0 keeps everything
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

## Retention

Nothing deletes a notification on its own. Deleting history on a timer nobody
configured is the kind of surprise a starter should not ship, so retention is a
command you schedule:

```bash
manage.py notifications_prune              # the configured window
manage.py notifications_prune --days 90    # override it for one run
manage.py notifications_prune --dry-run    # count without deleting
```

`DJANGO_NOTIFICATIONS_RETENTION_DAYS` defaults to `0`, which means keep
everything — and with no window set the command **refuses** rather than guessing
how much history to remove. Receipts go with the notifications they belong to,
by cascade.

## Using it

Creating a notification is a function call, and the socket push happens on save:

```python
from apps.notifications.events import notify_everyone, notify_user, notify_users

notify_user(user, subject="Your export is ready", link="/exports/42")
notify_everyone(subject="Maintenance at 22:00 UTC", level="warning")
notify_users(team, subject="Deploy finished", level="success")
```

`notify_users` is one row per recipient, not one shared row, because read state
is per account and a shared row would have to invent a roster of who it was for.
It loops rather than using `bulk_create` — `bulk_create` skips `post_save`, so
every notification would be saved and none of them delivered, which is exactly
the failure the signal exists to prevent. A send long enough for that to hurt
belongs in a task queue.

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
  switch (frame.type) {
    case "ready":
      if (!frame.authenticated && accessToken) {
        socket.send(JSON.stringify({ command: "authenticate", token: accessToken }));
      }
      break;
    case "authenticated":
      setBadge(frame.unread);
      break;
    case "notification":
      render(frame.notification);
      break;
    case "state":            // another device of this account moved the read state
      setBadge(frame.unread);
      break;
    case "read":
    case "dismiss":
      setBadge(frame.unread);
      break;
    case "error":
      console.warn(frame.title, frame.description);
      break;
  }
};

// The tray, over the same socket -- no second transport needed.
const openTray = () => socket.send(JSON.stringify({ command: "list", limit: 20 }));
const dismiss = (id) => socket.send(JSON.stringify({ command: "dismiss", id }));
```

Opening it before sign-in and authenticating afterwards is the intended shape: no
reconnect, and announcements render for a visitor who never signs in at all.

Reading it back over HTTP is the fallback for a client that was not connected:
the socket catches a reconnecting client up with its unread backlog
(`DJANGO_NOTIFICATIONS_SOCKET_BACKLOG`, twenty by default), and the list
endpoint holds the rest of the history.
