# Support

Live chat and support tickets between a client and the desk, over four
transports at once. Optional in the same way every login method is: naming it in
`DJANGO_SUPPORT_ENABLED` is what installs it, and a project that does not name it
carries no support tables, no routes, no socket and never imports the package.

Like the [CMS](cms.md), the [notifications](notifications.md) and the
[shop](shop.md), it is a **feature app**: it lives in `src/apps/support`, and the
directory can be copied into another Django project or deleted from this one
without leaving a hole. Its own
[`README`](../src/apps/support/README.md) is the drop-it-in-elsewhere guide.

## A ticket is a conversation

That one decision is the whole design, and it is what lets live chat and a
formal support ticket be one app rather than two that have to be kept in step.

A **ticket** is a thread. A **message** is something somebody said in it. `kind`
says what the thread is, and there are five of them in two families:

| `kind` | What it means | Who can see it |
| --- | --- | --- |
| `chat` | Somebody opened a widget and expects an answer now | The people in it, and all staff |
| `ticket` | A filed problem with a paper trail | The people in it, and all staff |
| `channel` | An open room anybody signed in may find and join | Anybody signed in |
| `group` | A private room created with its members | Its members only |
| `direct` | A private chat between exactly two accounts | Those two only |

Nothing about the storage differs between them. A chat that turns out to be a
real problem is **promoted** by giving it a subject and a category — not by
copying rows into another table, which is the version of this that goes wrong.

### The desk, and the rooms

The first two are **desk kinds**: somebody talking to the organisation. Staff
see every one of them, because working the queue is the job, and that is what
`is_staff` buys.

The last three are **rooms**: people talking to each other. Staff have no
standing in them whatsoever. A support agent is not entitled to read a private
message between two customers because their account has a flag set — a desk that
could do that would be a surveillance tool with a help widget attached. So:

- a group and a direct message are visible to their members and to nobody else,
  staff included, and an id somebody guessed is answered with a 404;
- a channel is visible to anybody signed in, because a room nobody can find is
  a room nobody can join;
- the desk's own verbs — claim, assign, prioritise, tag, rate — refuse a room
  outright, so a channel can never land in an agent's queue;
- the queue and your own thread list never include a channel you have not
  joined. `GET /support/channels` is the only listing in the app that shows you
  something you are not already part of.

`TicketQuerySet.visible_to` is the single place this line is drawn, and
`listed_for` the single place the narrower "what is in my list" question is
answered. Every rule about who may do what is decided once, in `services.py`, so
the transports cannot disagree about who may close a ticket or read a note.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/support` | Bearer | List the conversations this account can see |
| `POST` | `/api/v1/support` | Bearer | Open a conversation |
| `GET` | `/api/v1/support/canned-replies` | Bearer | List the desk's saved replies |
| `GET` | `/api/v1/support/categories` | Bearer | List what a ticket can be about |
| `GET` | `/api/v1/support/channels` | Bearer | List the open channels |
| `POST` | `/api/v1/support/channels` | Bearer | Open a channel |
| `POST` | `/api/v1/support/direct` | Bearer | Open a private chat |
| `POST` | `/api/v1/support/groups` | Bearer | Open a private group |
| `DELETE` | `/api/v1/support/messages/{message_id}` | Bearer | Retract a message |
| `PATCH` | `/api/v1/support/messages/{message_id}` | Bearer | Rewrite your own message |
| `GET` | `/api/v1/support/stats` | Bearer | The numbers the desk runs on |
| `GET` | `/api/v1/support/tags` | Bearer | List the desk's tags |
| `GET` | `/api/v1/support/unread` | Bearer | Count what is waiting |
| `POST` | `/api/v1/support/uploads` | Bearer | Send a file, before sending a message |
| `GET` | `/api/v1/support/{ticket_id}` | Bearer | Read one conversation |
| `POST` | `/api/v1/support/{ticket_id}/assign` | Bearer | Give a conversation to an agent |
| `POST` | `/api/v1/support/{ticket_id}/claim` | Bearer | Take a conversation yourself |
| `POST` | `/api/v1/support/{ticket_id}/close` | Bearer | Close a conversation |
| `POST` | `/api/v1/support/{ticket_id}/join` | Bearer | Join a channel |
| `POST` | `/api/v1/support/{ticket_id}/leave` | Bearer | Leave a room |
| `GET` | `/api/v1/support/{ticket_id}/messages` | Bearer | Read a conversation's messages |
| `POST` | `/api/v1/support/{ticket_id}/messages` | Bearer | Say something in a conversation |
| `POST` | `/api/v1/support/{ticket_id}/notes` | Bearer | Leave a staff-only note |
| `POST` | `/api/v1/support/{ticket_id}/participants` | Bearer | Add somebody to a conversation |
| `POST` | `/api/v1/support/{ticket_id}/priority` | Bearer | Reprioritise a conversation |
| `POST` | `/api/v1/support/{ticket_id}/rating` | Bearer | Rate a settled conversation |
| `POST` | `/api/v1/support/{ticket_id}/read` | Bearer | Mark a conversation read |
| `POST` | `/api/v1/support/{ticket_id}/reopen` | Bearer | Reopen a conversation |
| `POST` | `/api/v1/support/{ticket_id}/status` | Bearer | Move a conversation |
| `POST` | `/api/v1/support/{ticket_id}/tags` | Bearer | Replace a conversation's tags |
| `POST` | `/api/v1/support/{ticket_id}/typing` | Bearer | Say you are typing |
| `POST` | `/api/v1/support/{ticket_id}/unread` | Bearer | Mark a conversation unread |
<!-- /generated:routes -->

Everything is scoped to the caller. A conversation somebody else opened is a
**404** rather than a 403: a stranger guessing ids should not learn which ones
exist.

The same surface is published four ways — these endpoints, the
[socket](#the-socket-protocol), GraphQL and gRPC — under the same names, with
the same arguments and the same replies, and refusals carry the same titles
everywhere, so a client that has learned what `NOT_FOUND` means from one
transport does not learn it again from another. That includes the rooms:
`channels`, `create_channel`, `create_group`, `direct`, `join` and `leave` exist
on all four, as `supportChannels` / `createSupportChannel` / `createSupportGroup`
/ `openSupportDirect` / `joinSupportRoom` / `leaveSupportRoom` in GraphQL and as
`Channels` / `CreateChannel` / `CreateGroup` / `Direct` / `Join` / `Leave` in
gRPC. The visibility rules live in `services.py`, so a group is a 404 to the
desk whichever door it knocks on.

### An API walkthrough

Every call carries the same bearer token the rest of the API takes, and every
response is wrapped in [the standard envelope](responses.md) — `data` below is
that envelope's payload.

```bash
TOKEN=...   # from signing in; see docs/signing-in.md
API=https://example.com/api/v1/support
AUTH="Authorization: Bearer $TOKEN"
```

**Open a ticket.** A subject is required for a `ticket` and pointless for a
`chat`, and the category is what sets the deadlines:

```bash
curl -X POST -H "$AUTH" -H 'Content-Type: application/json' "$API" \
  -d '{"kind": "ticket", "subject": "I was charged twice",
       "body": "There are two charges on the 3rd.", "category": "billing"}'
```

```json
{"data": {"id": "6f2c…", "reference": "SUP-9C37A1", "kind": "ticket",
  "subject": "I was charged twice", "status": "open", "priority": "normal",
  "sla": {"first_response_due_at": "2026-09-08T12:04:11+00:00", "breached": false},
  "unread": 0, "participants": [{"role": "client", "user": {"username": "clara"}}]}}
```

The **reference** is the short string somebody reads down a telephone. The id is
what every other call takes.

**The queue**, which is the same endpoint answering a different question
depending on who is asking:

```bash
curl -H "$AUTH" "$API"                          # a client: their own threads
curl -H "$AUTH" "$API?status=open&mine=true"    # an agent: what is theirs
curl -H "$AUTH" "$API?unassigned=true"          # what nobody has picked up
curl -H "$AUTH" "$API?breached=true"            # what the desk is late on
curl -H "$AUTH" "$API?live=true"                # open, pending or on hold
curl -H "$AUTH" "$API?search=charged"           # subject, body and reference
```

**Say something**, and read what has been said:

```bash
curl -H "$AUTH" "$API/$ID/messages"
curl -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  "$API/$ID/messages" -d '{"body": "Any news?"}'
```

**A staff-only note** goes into the same thread, in the same order, and is
dropped from every list a client can reach:

```bash
curl -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  "$API/$ID/notes" -d '{"body": "Refund approved, see ticket 41."}'
```

**Attach a file.** Two steps, because a WebSocket frame is JSON and cannot carry
a multipart body — so the upload is the one half that has to be HTTP, and the
message naming it can go over any transport:

```bash
curl -X POST -H "$AUTH" -F file=@screenshot.png "$API/uploads"
# → {"data": {"id": "b81f…", "name": "screenshot.png", "url": "/media/…"}}

curl -X POST -H "$AUTH" -H 'Content-Type: application/json' "$API/$ID/messages" \
  -d '{"body": "Here is what I see.", "upload_ids": ["b81f…"]}'
```

An upload may be claimed exactly once, by its owner. Naming somebody else's id
is a refusal rather than a way to read their file.

**The desk's own verbs**, all refused for a client with `FORBIDDEN`:

```bash
curl -X POST -H "$AUTH" "$API/$ID/claim"
curl -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  "$API/$ID/assign" -d '{"agent": "…"}'
curl -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  "$API/$ID/priority" -d '{"priority": "urgent"}'
curl -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  "$API/$ID/tags" -d '{"tags": ["escalated"]}'
curl -H "$AUTH" "$API/stats"
```

`tags` is a **replacement**, not an add: a tag picker sends the set it is now
showing and does not have to work out the difference from what it was showing
before.

**Settling it.** Either side may close; only the client may rate, and only once
it is settled:

```bash
curl -X POST -H "$AUTH" "$API/$ID/close"
curl -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  "$API/$ID/rating" -d '{"score": 5, "comment": "Quick."}'
curl -X POST -H "$AUTH" "$API/$ID/reopen"
```

`pending` and `on_hold` are statements about what the *desk* is waiting for, so
a client setting one is refused. Resolve, close and reopen are shared.

## Read state

One row per person per thread carrying `last_read_at`, rather than a receipt per
message. That answers "how many did I miss?" with a count instead of a join
against every message anybody ever sent, and it is the only shape that stays
cheap when a busy thread has a thousand messages and four people in it.

```bash
curl -H "$AUTH" "$API/unread"          # {"messages": 3, "tickets": 2}
curl -X POST -H "$AUTH" "$API/$ID/read"
curl -X POST -H "$AUTH" "$API/$ID/unread"
```

`read` **never moves the watermark backwards**, and does not move it at all when
there was nothing unread. Both matter to a client that fires it more often than
it needs to — which every client does, because "mark read" is what a scroll
handler calls. The reply says `changed`, so "you have caught up" and "there was
nothing to catch up on" stay different answers.

## The SLA

Stored as two deadlines on the ticket, never as a breach flag. A flag would need
something to set it — a cron job, a signal, a worker — and would be wrong for the
whole window between the deadline passing and that thing running. The deadlines
are written when the ticket is created and compared against the clock when
anybody asks, so a breach is true the instant it is true.

The windows live on the **category**, because that is where the promise is
actually made: "billing questions are answered within an hour" is a statement
about billing. A ticket copies the windows it was created under into its own
deadlines, so editing the category later does not rewrite the promise made to
tickets already open under it.

A category with zero minutes promises nothing, which is the default and is what
a chat gets.

## Running the socket

It is a plain ASGI application — no Channels — mounted at `DJANGO_SUPPORT_WS_PATH`
(`/ws/support` by default) by [`config/sockets.py`](../src/config/sockets.py).
`manage.py runserver` is WSGI and will never serve it; use `make serve`, which is
uvicorn with the development settings. See
[the notifications page](notifications.md#running-the-socket) for the full note
on the `asgi` extra — a missing WebSocket implementation looks exactly like a
socket that was never mounted.

## The socket protocol

**This socket admits nobody it cannot name.** The notification socket accepts
anonymous connections because it has genuinely public traffic to deliver. This
one has none: every frame it sends belongs to a named conversation — a client
and the desk, a private chat, a group, or a channel somebody joined. So a
handshake carrying no usable credential is **closed rather than accepted**, with
close code `1008`, before any accept. An ASGI server turns that into an HTTP 403
on the upgrade, so the client sees a failure instead of a socket that opens and
never speaks.

There is therefore no signing in over the socket and no signed-out state: no
`authenticate` command, no `deauthenticate`, and no handler that has to wonder
whether anybody is there. A page that opens the socket before it has a token
should open it afterwards instead. A connection belongs to one account for its
whole life; serving two people down one socket was the thing mid-connection
sign-in made possible and nothing wanted.

Credentials in the handshake are read from the same four places, and in the same
order, as [the notification socket](notifications.md#the-socket-protocol):
`?token=`, a bearer subprotocol, an `Authorization` header, a session cookie.

**Connecting subscribes you to your own world.** The connection joins your
account channel, the desk channel if you are staff, and the channels of the
threads you are currently in — bounded, newest first. From that moment one socket
carries every conversation you are part of, and a client renders a list of
threads and any one of them open without opening a second connection. A thread
outside that window is joined with `subscribe`, which answers with its tail so
the client has something to render immediately.

**Confidentiality is applied on the way out.** An internal note is published to
the thread's channel like every other message and dropped for a connection whose
account may not read it. The alternative — a second channel per thread — doubles
every agent's subscriptions and moves the rule somewhere nothing can test end to
end.

**The socket can do everything the HTTP API can**, with one exception, and it is
a protocol limit rather than a choice: a file has to be uploaded over HTTP.

### What the client sends

Every frame is a JSON object with a `command`. Every connection has an account
— the handshake saw to that — so none of them has a signed-out case:

| Command | Answered with |
| --- | --- |
| `{"command": "ping"}` | `pong` — for holding an idle connection open through a proxy |
| `{"command": "whoami"}` | `whoami` — still worth asking after a sleep |

Reading:

| Command | Answered with |
| --- | --- |
| `tickets` (the queue filters, under the same names as the endpoint) | `tickets` |
| `ticket`, `messages` | `ticket`, `messages` |
| `unread`, `categories` | `unread`, `categories` |
| `subscribe`, `unsubscribe` | `subscribed` (with the thread's tail), `unsubscribed` |

Talking:

| Command | Answered with |
| --- | --- |
| `open` | `opened` — and the connection is subscribed in the same round trip |
| `send`, `note` | `sent` |
| `edit`, `delete` | `edited`, `deleted` |
| `read`, `unread_ticket` | `read`, `unread_ticket` |
| `typing`, `presence` | `typing_ack`, `presence_ack` |

Either side: `status`, `close`, `reopen`, `rate`. The desk's own: `assign`,
`claim`, `priority`, `tag`, `invite`, `tags`, `canned`, `stats` — and every one
of those refuses a room, because none of it is queue work.

Rooms, on the same connection:

| Command | Answered with |
| --- | --- |
| `channels` (optional `search`) | `channels` — every open channel, each flagged `joined` or not |
| `create_channel` (`name`, optional `slug`, `body`) | `created` — and this connection is subscribed in the same round trip |
| `create_group` (`name`, `members`, optional `body`) | `created` |
| `direct` (`account`) | `created` — the chat you already had, if there was one |
| `join` (`ticket`) | `joined` — a channel only, and idempotent |
| `leave` (`ticket`) | `left` — a channel or a group; a private chat cannot be left |

One connection therefore carries all four lists — the desk's threads, the
channels you are in, your groups and your private chats — because they are one
table and one subscription model. A client renders the lot without opening a
second socket.

Somebody put into a room they were not in is told on their **own account
channel**, not the room's, because they are not subscribed to the room yet. That
frame is a `ticket` with `reason: "invited"`, and the client answers it by
sending `subscribe`.

Sending into a thread this connection had not joined subscribes it, so an agent
answering out of a queue starts hearing the reply without a second command.

### What the server sends

| Frame | When |
| --- | --- |
| `ready` | On connect, with the account the connection belongs to and its badge |
| `message` | Somebody said something in a thread this connection is in |
| `ticket` | A thread changed — status, assignee, priority, tags |
| `read` | Somebody's watermark moved, so a second device can grey out the badge |
| `typing`, `presence` | Somebody started typing, or arrived in or left a thread |
| `unread` | This account's badge changed |
| `created`, `joined`, `left` | A room was opened, joined or left |
| `channels` | The channel directory |
| `error` | A refusal. **A frame, not a close** — a mistyped id should cost one message, not the conversation flowing over the connection |

Error titles are the same vocabulary the HTTP API answers with —
`AUTHENTICATION_REQUIRED`, `BAD_REQUEST`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`,
`TOKEN_INVALID` — so a client translates one set of strings rather than two.

## Models

<!-- generated:models -->
#### `Attachment`

One file, attached to one message.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `message` | ForeignKey | → `support.Message` |
| `upload` | OneToOne | unique, → `support.Upload`, nullable |
| `name` | Char |  |
| `url` | Char |  |
| `content_type` | Char |  |
| `size` | PositiveBigInteger |  |
| `created_at` | DateTime | not editable |

#### `CannedReply`

Something the desk says often enough to have written down once.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `title` | Char | unique |
| `body` | Text |  |
| `category` | ForeignKey | → `support.Category`, nullable |
| `is_active` | Boolean |  |
| `used_count` | PositiveInteger | not editable |

#### `Category`

What a ticket is about, and what the desk has promised about it.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `name` | Char | unique |
| `slug` | Slug | unique |
| `description` | Text |  |
| `default_priority` | Char |  |
| `first_response_minutes` | PositiveInteger |  |
| `resolution_minutes` | PositiveInteger |  |
| `is_active` | Boolean |  |
| `order` | PositiveInteger |  |

#### `Message`

Something said in a thread -- by a person, or by the system on their behalf.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `ticket` | ForeignKey | → `support.Ticket` |
| `author` | ForeignKey | → `accounts.User`, nullable |
| `kind` | Char |  |
| `visibility` | Char |  |
| `body` | Text |  |
| `data` | JSON |  |
| `created_at` | DateTime | not editable |
| `edited_at` | DateTime | not editable, nullable |
| `deleted_at` | DateTime | not editable, nullable |

#### `Participant`

One account's membership of one thread, and how far through it they are.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `ticket` | ForeignKey | → `support.Ticket` |
| `user` | ForeignKey | → `accounts.User` |
| `role` | Char |  |
| `joined_at` | DateTime | not editable |
| `last_read_at` | DateTime | nullable |
| `notify` | Boolean |  |

#### `Tag`

A label the desk puts on a thread for its own purposes.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `name` | Char | unique |
| `slug` | Slug | unique |
| `colour` | Char |  |

#### `Ticket`

One conversation between a client and the desk.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `reference` | Char | unique, not editable |
| `kind` | Char |  |
| `client` | ForeignKey | → `accounts.User` |
| `subject` | Char |  |
| `slug` | Slug | unique, nullable |
| `direct_key` | Char | unique, not editable, nullable |
| `category` | ForeignKey | → `support.Category`, nullable |
| `status` | Char |  |
| `priority` | Char |  |
| `assignee` | ForeignKey | → `accounts.User`, nullable |
| `data` | JSON |  |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `last_message_at` | DateTime | not editable, nullable |
| `first_response_at` | DateTime | not editable, nullable |
| `resolved_at` | DateTime | not editable, nullable |
| `closed_at` | DateTime | not editable, nullable |
| `first_response_due_at` | DateTime | not editable, nullable |
| `resolution_due_at` | DateTime | not editable, nullable |
| `rating` | PositiveSmallInteger | nullable |
| `rating_comment` | Text |  |
| `rated_at` | DateTime | not editable, nullable |

#### `Upload`

A file somebody has sent but not yet attached to anything.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `owner` | ForeignKey | → `accounts.User` |
| `name` | Char |  |
| `url` | Char |  |
| `content_type` | Char |  |
| `size` | PositiveBigInteger |  |
| `created_at` | DateTime | not editable |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `Attachment` | No — read-only | — | `name`, `ticket`, `size`, `content_type`, `created_at` |
| `CannedReply` | Yes | — | `title`, `category`, `is_active`, `used_count` |
| `Category` | Yes | — | `name`, `slug`, `default_priority`, `first_response_promise`, `resolution_promise`, `is_active`, `tickets` |
| `Message` | Yes | — | `created_at`, `ticket`, `author`, `kind`, `visibility`, `preview` |
| `Participant` | No — read-only | — | `user`, `ticket`, `role`, `joined_at`, `last_read_at`, `notify` |
| `Tag` | Yes | — | `name`, `slug`, `swatch`, `tickets` |
| `Ticket` | Yes | `mark_resolved`, `mark_closed`, `unassign` | `reference`, `subject_or_kind`, `client`, `status_badge`, `priority_badge`, `assignee`, `waiting`, `sla_state`, `messages` |
| `Upload` | No — read-only | — | `name`, `owner`, `size`, `content_type`, `claimed`, `created_at` |
<!-- /generated:admin -->

The queue's **SLA column** is not sortable, and that is on purpose: a breach is
computed against the clock rather than stored, so there is no column to sort on.
Sorting by `first_response_due_at` is the orderable version of the same question
and is one click away in the list filter.

The bulk actions are only the three that are safe to do to a hundred rows at
once. Assigning in bulk is not among them: giving one agent everything selected
is rarely what anybody meant and is not undoable from that screen. The two that
settle tickets **save each row** rather than calling `update`, because `update`
skips `post_save` and every client watching one of those threads would be left
showing it as open forever.

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_SUPPORT_BROKER` | Recommended | how a message posted in one process reaches sockets held by another. |
| `DJANGO_SUPPORT_RETENTION_DAYS` | Optional | how long a closed ticket is kept before `manage.py support_prune` deletes it. 0 or more. |
| `DJANGO_SUPPORT_MAX_UPLOAD_MB` | Optional | the largest file a client may attach to a message. 0 or more. |
| `DJANGO_SUPPORT_SOCKET_BACKLOG` | Optional | how many recent messages per thread a client is caught up with on connect. Range 0–200. |
<!-- /generated:settings -->

```bash
DJANGO_SUPPORT_ENABLED=true
DJANGO_SUPPORT_BROKER=apps.support.broadcast.RedisBroker   # in production
DJANGO_SUPPORT_REDIS_URL=redis://127.0.0.1:6379/0          # defaults to DJANGO_AUTH_REDIS_URL
DJANGO_SUPPORT_WS_PATH=/ws/support                         # tell your proxy the same
DJANGO_SUPPORT_CHANNEL_PREFIX=support                      # namespaces the Redis channels
DJANGO_SUPPORT_REFERENCE_PREFIX=SUP                        # the SUP- in SUP-9C37A1
DJANGO_SUPPORT_SOCKET_BACKLOG=30                           # threads auto-subscribed on sign-in
DJANGO_SUPPORT_MAX_UPLOAD_MB=10                            # 0 means no limit here
DJANGO_SUPPORT_UPLOAD_EXTENSIONS=png,jpg,pdf               # empty allows anything
DJANGO_SUPPORT_RETENTION_DAYS=0                            # 0 keeps everything
```

Only the first line is needed to start.

The **broker** is the setting worth reading twice, and it matters more here than
it does for notifications. A message is written in ordinary synchronous request
code and has to reach sockets held open somewhere else. The default
`MemoryBroker` fans out inside one process, which is what lets the app work the
moment it is enabled — and under two workers a client and the agent answering
them are very likely to be on different workers and hear nothing at all.
`RedisBroker` is the same thing across a deployment, and `manage.py check` warns
while the default is still in place rather than leaving it to be discovered in
production by a customer.

## Retention

Nothing deletes a ticket on its own. Removing a customer's support history on a
timer nobody configured is the one thing a support desk must never do, so
retention is a window somebody sets and a command somebody schedules:

```bash
manage.py support_prune --dry-run
manage.py support_prune
manage.py support_prune --days 365 --upload-days 3
```

With no window set it refuses rather than treating zero as "delete everything".
Only **closed** tickets are pruned: an open one is somebody's unanswered
question however old it is, and `resolved` is the desk's opinion rather than the
client's agreement. Messages, attachments and participants go by cascade.

Staged uploads nobody ever attached have their own, much shorter window, because
they are not history — they are a file somebody picked and then changed their
mind about. A claimed upload is never deleted whatever its age.

The files themselves are left in storage, deliberately: deleting from a bucket is
not transactional, a half-done sweep is worse than none, and a project with a
storage lifecycle rule already has a better tool for it.
