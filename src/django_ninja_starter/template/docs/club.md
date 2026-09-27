# Club

Clubs with a levelled ladder, and missions that complete themselves from what
the rest of the deployment does. Optional in the same way every feature app is:
naming it in `DJANGO_CLUB_ENABLED` is what installs it, and a project that does
not name it carries no club tables, no routes and never imports the package.

Like the [CMS](cms.md), [notifications](notifications.md), the [shop](shop.md),
[support](support.md) and the [wallet](wallet.md), it lives in `src/apps/club`
and the directory can be copied into another Django project or deleted from this
one without leaving a hole.

It is the one app here designed to be told about what the *others* do, which is
the part worth reading first.

## Nobody claims a mission

The temptation with missions is an endpoint: `POST /missions/{id}/claim`, and a
client that says it did the thing. That is a mission a client can invent, and
there is no amount of validation that fixes it — the client is the party whose
claim is in question.

So there is no such endpoint. Instead the app where the thing *actually happens*
says so::

    from apps.club import track

    track(user, "shop.order.paid", value=order.total, reference=f"order:{order.pk}")

and everything after that — finding the missions listening for that event,
deciding which of them this satisfies, counting progress, paying XP, moving a
level, announcing it — happens inside the club app without the caller knowing any
of it exists.

`track` is the entire integration surface. It does nothing when the club app is
not installed, nothing when the account is in no club, and nothing when no
mission cares, so a call site can be added before any of that is true.

## Working with an app this starter has never seen

A mission is configured by an operator picking an event from a list. That list is
a registry, and anything can add to it — which is what makes this work for your
own code rather than only for the apps that ship here.

Declare what your app emits, anywhere that gets imported::

    # myapp/club_events.py
    from apps.club.events import EventSpec, register

    register(EventSpec(
        key="myapp.invoice.settled",
        label="An invoice was settled",
        description="Sent once, when payment clears.",
        value_label="The invoice total",
        fields={"customer_id": "Who it was for", "currency": "What it was priced in"},
        source="myapp",
    ))

name the module in `DJANGO_CLUB_EVENT_SOURCES`, and call `track` where it
happens. The event appears in `GET /club/events`, in the admin's dropdown, and in
every mission an operator writes from then on.

**A registry rather than free text**, because an operator typing
`myapp.invoice.setled` into a text field creates a mission that silently never
fires, and discovers it weeks later when nobody has earned anything. An
unregistered key is refused at both ends: when the mission is written, and when
`track` is called with it.

**Registering is not importing.** The registry holds strings and descriptions, so
`apps.club` does not import the shop to know about `shop.order.paid` — the shop
announces itself. The built-in bridges in `apps/club/bridges/` are each guarded
by `apps.is_installed`, so a deployment running the club without the shop
registers no shop events, and a mission referring to one is refused rather than
quietly dead.

Every built-in bridge has the same shape, and it is the one to copy: declare the
events, then connect a receiver to signals the emitting app already sends.
`bridges/wallet.py` listens to the wallet's `entry_settled`, `bridges/shop.py` to
the shop's `order_paid` and `review_published`, and `bridges/accounts.py` to the
row every login method writes to the sign-in audit trail. None of those apps
imports the club or calls `track`, so none of them had to change to be heard. An
app of yours that announces nothing calls `track` where the thing happens instead.

`accounts.user.registered` is heard from the user table rather than the audit
trail, so an account created by a social callback, the admin or
`createsuperuser` counts as well. An account is in no club at the moment it
exists, so set `DJANGO_CLUB_JOIN_ON_SIGNUP` to a club's slug to put every new
account in it first -- without it, a mission on that event has nobody to pay. A
slug naming no club, or a club not taking members, is logged and never fails the
sign-up.

## An account is in one club

Not a rule a service checks — a `OneToOneField` on `Membership.user`. Two
requests naming two different clubs both pass any check a service could make, and
the database refuses the second, which is the only arbiter that is still right
with four processes running.

Joining the club you are already in returns that membership rather than refusing:
a double-tapped button is not an error. Joining a *different* one is a `409`,
because leaving is a decision and making it a side effect of joining would let
somebody lose a ladder they had spent months on by tapping the wrong card.

**Leaving keeps everything.** The membership row stays, marked `left`, so the XP
and the award history survive and rejoining picks the ladder back up where it was
left. A club where leaving reset your progress is a club where the way out of a
mistake is a thing people warn each other about.

**A suspension is not left behind.** A suspended member's `leave` is a `409`: a
suspension that leaving and joining again could lift is one that lasts two
requests. Only an operator lifts it. `leave` re-reads the membership under its
row lock, so a suspension saved while a leave is in flight still stands.

**The back office adds members through the service.** The admin's *add
membership* form calls `ClubService.add_member`, whatever the join policy, so an
invite-only club has a way in, a member who left one can be put back, and the join
is announced and tracked like any other. The form refuses setting a membership to
`left`: leaving goes through the service, which stamps `left_at` and announces it.

**XP belongs to the club that paid it.** Every award records the club it was
earned in, and a member's XP is the sum of their current club's awards. Moving to
another club starts its ladder from zero, and coming back to the first finds the
XP still there. Otherwise joining a new club would be a way to arrive at the top
of its ladder.

## The ladder is a ladder

Levels are numbered from one with no gaps, the first needs zero XP, and each
needs strictly more than the one below it. `xp_required` is a **threshold, not a
cost**: reaching level four does not spend the XP that got you there.

| Field | What it is |
| --- | --- |
| `position` | The rung's number, from 1, contiguous |
| `name` | What it is called: Bronze, Gold |
| `xp_required` | Lifetime XP that reaches it |
| `logo` | The badge |
| `perks` | What the member gets, in words a member reads |

A ladder with a gap at three is not a stricter ladder, it is an ambiguous one,
and the ambiguity surfaces as members sitting at a level the club never meant to
exist. So it is refused in both places a ladder can be written: `set_levels`
checks the whole set, and the admin's inline formset checks it again, because the
admin saves one row at a time and one row cannot see the shape of the set.

A member with more XP than the top rung sits on the top rung rather than falling
off the end, and reports `progress` of `1.0` — finished, not at the beginning of
nothing.

## XP is not a column

It is the sum of the awards a member has been given, for the reason the wallet
has no balance column: a stored total is a second copy of a fact the awards
already hold, and the two disagree the first time a process dies between writing
an award and updating the total.

XP granted by hand is written from the grant screen linked on a member's page in
the admin, through the same service, with a reason and the operator's name on the
award. The screen issues the reference when it is drawn, so a form submitted twice
grants once. Reusing a reference for a different grant -- another amount, or a
member who has since moved club -- is refused rather than reported as paid.

A caller's `reference` longer than 150 characters is hashed before it is stored,
since it becomes part of a 200-character award key.

Awards are immutable and idempotent — `reference` is unique per member — so a
mission completion replayed by a retried request, a redelivered webhook or a job
run twice pays once. `GET /club/awards` publishes the ledger, so a member can add
it up and get the number the app reports, which is the point of deriving it.

Progress is idempotent in the same way. A mission needing three events counts
each `reference` once, so one event delivered three times is one step towards it
rather than a completion.

## What a mission can ask for

| Field | What it does |
| --- | --- |
| `event` | The registered key it listens for |
| `criteria` | Which of those events count. Empty means all |
| `xp` | What completing it pays |
| `target_count` | How many matching events complete it once |
| `repeat` | `once`, `every_time`, `daily`, `weekly` |
| `starts_at` / `ends_at` | The window it runs in |

`criteria` is a small object, deliberately not an expression language — an
expression language stored in a column and evaluated at runtime is a remote code
execution waiting for somebody with admin access to have a bad day. Five
comparisons, and several together are an AND:

```json
{"min_value": 100, "equals": {"currency": "USD"}, "exists": ["coupon"]}
```

A key that is not one of `min_value`, `max_value`, `equals`, `in` or `exists` is
**refused when the mission is saved** rather than ignored when it runs. An
ignored typo is a mission paying XP for every event of its kind, which is the
opposite of what the operator wrote.

## Three transports, one set of rules

REST, GraphQL and gRPC publish the same contract three ways, because every
decision is `ClubService`'s and the doors only translate. The names follow each
transport's convention, and gRPC renames two calls whose natural name collides
with a message -- `club` is `GetClub` there and `leaderboard` is
`LeaderboardCall` -- because the generator resolves actions and messages out of
one registry.

**No transport completes a mission.** There is no `claim` endpoint, no
`clubCompleteMission` mutation and no such gRPC call, and each transport has a
test asserting the absence rather than trusting it. A mission a client can
report is a mission a client can invent.

## Routes

Everything is scoped to the caller. No endpoint takes a membership id, and the
leaderboard is your own club's or nothing — a club is a social object, and an API
that let any account list any club's members would be a directory of everybody
who uses the deployment. For the same reason a leaderboard row's `username` is
not the login username: it is the first word and last initial of the profile's
display name ("Ada L."), or `Member` when there is none, so the leaderboard is not a
list of sign-in identifiers to try passwords against.

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/club/awards` | Bearer | Every XP you have been paid |
| `GET` | `/api/v1/club/clubs` | Bearer | Every club this deployment runs |
| `GET` | `/api/v1/club/clubs/{slug}` | Bearer | One club and its ladder |
| `GET` | `/api/v1/club/clubs/{slug}/levels` | Bearer | One club's ladder |
| `GET` | `/api/v1/club/events` | Bearer | What a mission can be built out of |
| `POST` | `/api/v1/club/join` | Bearer | Join a club |
| `GET` | `/api/v1/club/leaderboard` | Bearer | Your club, by XP |
| `POST` | `/api/v1/club/leave` | Bearer | Leave your club |
| `GET` | `/api/v1/club/me` | Bearer | Where this account stands |
| `GET` | `/api/v1/club/missions` | Bearer | What your club pays for |
<!-- /generated:routes -->

## Settings

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_CLUB_ENABLED` | **Yes** | whether this deployment carries clubs at all -- their tables, their routes and their admin. |
| `DJANGO_CLUB_EVENT_SOURCES` | Optional | dotted module paths that register your own app's events, so missions can be built out of what your code does. |
| `DJANGO_CLUB_JOIN_ON_SIGNUP` | Optional | the slug of a club every new account is put in as it is created, so a welcome can be earned by signing up. |
| `DJANGO_CLUB_PAGE_SIZE` | Optional | how many rows a listing returns when the caller does not say. Range 1–200. |
| `DJANGO_CLUB_MAX_PAGE_SIZE` | Optional | the ceiling on `limit`, so one request cannot ask for every member. Range 1–1000. |
<!-- /generated:settings -->

## Models

<!-- generated:models -->
#### `Club`

One club: a name, a look, and the ladder and missions defined against it.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `name` | Char |  |
| `slug` | Slug | unique |
| `description` | Text |  |
| `logo` | Char |  |
| `status` | Char |  |
| `join_policy` | Char |  |
| `metadata` | JSON |  |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |

#### `ClubLevel`

One rung: what it is called, what it looks like, and the XP that reaches it.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `club` | ForeignKey | → `club.Club` |
| `position` | PositiveInteger |  |
| `name` | Char |  |
| `xp_required` | PositiveInteger |  |
| `logo` | Char |  |
| `perks` | Text |  |
| `metadata` | JSON |  |
| `created_at` | DateTime | not editable |

#### `CountedOccurrence`

One occurrence that already moved one member's progress on one mission.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | BigAuto | primary key |
| `progress` | ForeignKey | → `club.MissionProgress` |
| `reference` | Char |  |
| `created_at` | DateTime | not editable |

#### `Membership`

One account's place in one club.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `user` | OneToOne | unique, → `accounts.User` |
| `club` | ForeignKey | → `club.Club` |
| `status` | Char |  |
| `joined_at` | DateTime | not editable |
| `left_at` | DateTime | nullable |
| `updated_at` | DateTime | not editable |

#### `Mission`

Something a member can do that the app notices by itself, and what it pays.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `club` | ForeignKey | → `club.Club` |
| `code` | Slug |  |
| `title` | Char |  |
| `description` | Text |  |
| `event` | Char |  |
| `criteria` | JSON |  |
| `xp` | PositiveInteger |  |
| `repeat` | Char |  |
| `target_count` | PositiveInteger |  |
| `is_enabled` | Boolean |  |
| `starts_at` | DateTime | nullable |
| `ends_at` | DateTime | nullable |
| `metadata` | JSON |  |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |

#### `MissionProgress`

How far one member has got with one mission, and when they last finished it.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `membership` | ForeignKey | → `club.Membership` |
| `mission` | ForeignKey | → `club.Mission` |
| `count` | PositiveInteger |  |
| `completions` | PositiveInteger |  |
| `last_completed_at` | DateTime | nullable |
| `updated_at` | DateTime | not editable |

#### `XpAward`

One payment of XP, kept forever. The sum of these is a member's XP.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `membership` | ForeignKey | → `club.Membership` |
| `club` | ForeignKey | → `club.Club` |
| `mission` | ForeignKey | → `club.Mission`, nullable |
| `xp` | PositiveInteger |  |
| `reason` | Char |  |
| `reference` | Char |  |
| `metadata` | JSON |  |
| `created_at` | DateTime | not editable |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `Club` | Yes | — | `name`, `slug`, `status`, `join_policy`, `levels_display`, `members_display` |
| `Membership` | Yes | `grant_ten_xp` | `user`, `club`, `status`, `xp_display`, `level_display`, `joined_at` |
| `Mission` | Yes | `enable_missions`, `disable_missions` | `title`, `club`, `event`, `xp`, `repeat`, `target_count`, `is_enabled` |
| `MissionProgress` | No — read-only | — | `membership`, `mission`, `count`, `completions`, `last_completed_at` |
| `XpAward` | No — read-only | — | `created_at`, `membership`, `club`, `xp`, `reason`, `mission` |
<!-- /generated:admin -->
