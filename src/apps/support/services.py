"""Everything a client or an agent can do, said once.

Four transports publish this app -- HTTP, GraphQL, gRPC and a WebSocket -- and
every one of them calls the class below. That is not tidiness: it is the only
way the four cannot drift. A permission rule written in a REST endpoint is a
permission rule the socket does not have, and the socket is the one an attacker
will find, because it is the one that carries the interesting traffic.

**Two roles, one method each.** Nearly every method here behaves differently for
a client and for a member of staff, and the difference is always made by
:meth:`SupportQueries._visible` or by an explicit :func:`_staff_only`, never by
the caller passing a flag. A transport cannot ask for the desk's view of a
queue; it can only pass the account that called, and what comes back is what
that account is entitled to.

**A client's world is the threads they opened.** They may read them, add to
them, close and reopen and rate them, and they may not see anybody else's, the
internal notes on their own, who a thread was reassigned between, or the desk's
queue statistics.

**An agent's world is the desk.** Every thread, every note, the assignment, the
priority, the tags and the numbers.

The refusals are four exception types, and each transport translates them once:
a missing row, a row somebody else's, a rule broken, an argument that was not
one. The first two are deliberately the same answer where a client is
concerned -- see :class:`TicketNotFound`.
"""

from typing import Any
from uuid import UUID

from django.db.models import Avg, Count, F, Q

from apps.support import events
from apps.support.models import (
    LIVE_STATUSES,
    CannedReply,
    Category,
    Kind,
    Message,
    MessageKind,
    Priority,
    Role,
    Status,
    Tag,
    Ticket,
    Upload,
    Visibility,
    assign,
    create_ticket,
    delete_message,
    edit_message,
    join,
    mark_read,
    mark_unread,
    post_message,
    rate,
    set_priority,
    set_status,
    unread_count,
)

MAX_PAGE = 200
DEFAULT_PAGE = 50


class TicketNotFound(LookupError):
    """No such thread -- or one belonging to somebody else.

    The two are deliberately the same answer: saying which is which would let
    anybody with a list of ids learn how many tickets the desk has and which of
    them exist, and would confirm the existence of another person's complaint.
    """


class MessageNotFound(LookupError):
    """No such message, or one in a thread this account cannot read."""


class NotPermitted(PermissionError):
    """The row exists and this account may see it, but not do this to it."""


class InvalidRequest(ValueError):
    """An argument that is not one: a status that does not exist, an empty message."""


def _staff_only(user: Any, what: str) -> None:
    """Refuse anybody who is not on the desk.

    Raises :class:`NotPermitted` rather than :class:`TicketNotFound`, because
    unlike another person's ticket, the existence of the desk is not a secret --
    a client knows perfectly well that agents and a queue exist, and a 403 that
    says so is more useful than a 404 that pretends otherwise.
    """
    if not getattr(user, "is_staff", False):
        raise NotPermitted(f"Only the support desk can {what}.")


def _choice(value: str, choices: Any, what: str) -> str:
    """Validate one enum argument, naming what would have been acceptable.

    Every transport takes these as strings -- a query parameter, a JSON field, a
    proto ``string`` -- so the validation belongs here rather than being done
    three times and forgotten once.
    """
    allowed = {member.value for member in choices}
    if value not in allowed:
        raise InvalidRequest(f"{value!r} is not a {what}. One of: {', '.join(sorted(allowed))}.")
    return value


def _body(body: str, *, uploads: int = 0) -> str:
    """A message has to say or carry something.

    An empty message with no attachment is not a message, and letting one
    through means every client has to render a blank bubble it cannot explain.
    An empty body *with* an attachment is fine: sending somebody a screenshot
    without a covering note is a normal thing to do.
    """
    text = body.strip()
    if not text and not uploads:
        raise InvalidRequest("A message needs a body or an attachment.")
    return text


class SupportService:
    """The desk and the client's view of it, behind one object."""

    # -- resolving what a caller may touch --------------------------------

    def _visible(self, user: Any) -> Any:
        """The threads this account may see at all. Nothing widens this."""
        return Ticket.objects.visible_to(user)

    def _ticket(self, user: Any, ticket_id: UUID) -> Ticket:
        ticket = (
            self._visible(user)
            .select_related("client", "assignee", "category")
            .prefetch_related("tags")
            .filter(pk=ticket_id)
            .first()
        )
        if ticket is None:
            raise TicketNotFound("No such ticket.")
        return ticket

    def _message(self, user: Any, message_id: UUID) -> Message:
        """One message, in a thread this account can read, that it may read.

        Two filters, not one. The thread has to be visible, and then the message
        within it has to be readable -- a client naming the id of an internal
        note on their own ticket is refused by the second, and gets the same
        answer as for a message that does not exist.
        """
        message = (
            Message.objects.filter(pk=message_id, ticket__in=self._visible(user))
            .readable_by(user)
            .select_related("ticket", "author")
            .first()
        )
        if message is None:
            raise MessageNotFound("No such message.")
        return message

    # -- reading ----------------------------------------------------------

    def _queue(
        self,
        user: Any,
        *,
        status: str | None = None,
        kind: str | None = None,
        priority: str | None = None,
        category: str | None = None,
        assignee: str | None = None,
        mine: bool = False,
        unassigned: bool = False,
        live: bool | None = None,
        search: str | None = None,
    ) -> Any:
        """Build the filtered queryset both :meth:`tickets` and :meth:`count` read.

        ``assignee``, ``mine`` and ``unassigned`` are three ways of asking about
        the same column because they are three different questions an agent
        actually asks -- "Ellie's queue", "my queue", "the pile nobody has
        picked up" -- and making a client compose them out of one parameter
        means every client composes them slightly differently.
        """
        tickets = self._visible(user)
        if status:
            tickets = tickets.filter(status=_choice(status, Status, "status"))
        if kind:
            tickets = tickets.filter(kind=_choice(kind, Kind, "kind"))
        if priority:
            tickets = tickets.filter(priority=_choice(priority, Priority, "priority"))
        if category:
            tickets = tickets.filter(category__slug=category)
        if live is True:
            tickets = tickets.live()
        elif live is False:
            tickets = tickets.settled()
        if mine:
            # For an agent, "mine" is what they were given; for a client it is
            # what they opened, which is everything they can see anyway. Both
            # readings are right for the person asking, and neither widens
            # anything -- `_visible` has already run.
            tickets = (
                tickets.assigned_to(user)
                if getattr(user, "is_staff", False)
                else tickets.for_client(user)
            )
        if unassigned:
            _staff_only(user, "read the unassigned queue")
            tickets = tickets.unassigned()
        if assignee:
            _staff_only(user, "filter by agent")
            tickets = tickets.filter(assignee_id=assignee)
        if search:
            tickets = tickets.for_search_by(user, search)
        return tickets

    def tickets(
        self,
        user: Any,
        *,
        limit: int = DEFAULT_PAGE,
        offset: int = 0,
        breached: bool | None = None,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """A page of threads, most recently active first, each with its unread count.

        ``breached`` is applied after the page rather than in the query, because
        a breach is computed against the clock and is not a column -- see
        :class:`apps.support.models.Ticket`. That is an honest trade: filtering
        in Python means the page can come back shorter than ``limit``, which is
        why ``total`` is reported separately and a client pages until it runs
        out rather than until a short page.
        """
        page = max(1, min(limit, MAX_PAGE))
        start = max(0, offset)
        tickets = (
            self._queue(user, **filters)
            .with_unread(user)
            .select_related("client", "assignee", "category")
            .prefetch_related("tags")
            .ordered_for_queue()
        )
        rows = [
            events.ticket_payload(ticket, unread=getattr(ticket, "unread", 0))
            for ticket in tickets[start : start + page]
        ]
        if breached is None:
            return rows
        return [row for row in rows if row["sla"]["breached"] is breached]

    def count(self, user: Any, **filters: Any) -> int:
        """How many threads :meth:`tickets` would page through, ignoring the page."""
        return self._queue(user, **filters).distinct().count()

    def ticket(self, user: Any, ticket_id: UUID) -> dict[str, Any]:
        """One thread, with this account's unread count and who else is in it.

        The participant list is trimmed for a client: they see the desk as
        "an agent", not as a roster of who has been reading their complaint.
        """
        ticket = self._ticket(user, ticket_id)
        payload = events.ticket_payload(ticket, unread=unread_count(ticket, user))
        payload["participants"] = self._participants(user, ticket)
        return payload

    def _participants(self, user: Any, ticket: Ticket) -> list[dict[str, Any]]:
        people = ticket.participants.select_related("user")
        if getattr(user, "is_staff", False):
            return [events.participant_payload(person) for person in people]
        return [
            events.participant_payload(person)
            for person in people
            if person.user_id in (ticket.client_id, ticket.assignee_id, user.pk)
        ]

    def messages(
        self,
        user: Any,
        ticket_id: UUID,
        *,
        limit: int = DEFAULT_PAGE,
        offset: int = 0,
    ) -> dict[str, Any]:
        """A page of a thread, oldest first, with the internal notes dropped for a client.

        Oldest first, unlike every other list in this project. A conversation is
        read forwards, and a client that had to reverse each page before
        rendering it would reverse it wrongly at least once.
        """
        ticket = self._ticket(user, ticket_id)
        page = max(1, min(limit, MAX_PAGE))
        start = max(0, offset)
        readable = (
            Message.objects.filter(ticket=ticket)
            .readable_by(user)
            .select_related("author")
            .prefetch_related("attachments")
        )
        return {
            "ticket": str(ticket.pk),
            "messages": [
                events.message_payload(message) for message in readable[start : start + page]
            ],
            "total": readable.count(),
            "limit": page,
            "offset": start,
        }

    def unread(self, user: Any) -> dict[str, Any]:
        """The badge: how many messages are waiting, and in how many threads."""
        threads = 0
        total = 0
        for ticket in self._visible(user).with_unread(user):
            count = getattr(ticket, "unread", 0)
            if count:
                threads += 1
                total += count
        return {"messages": total, "tickets": threads}

    def categories(self) -> list[dict[str, Any]]:
        """What a client may file a ticket under, and what the desk promised about each.

        Public in the sense that any signed-in account may read it: a client
        choosing where to file something has to be told the options, and the
        response times are a promise the desk is making rather than an internal
        target it would rather nobody quoted back.
        """
        return [
            {
                "id": str(category.pk),
                "name": category.name,
                "slug": category.slug,
                "description": category.description,
                "default_priority": category.default_priority,
                "first_response_minutes": category.first_response_minutes,
                "resolution_minutes": category.resolution_minutes,
            }
            for category in Category.objects.filter(is_active=True)
        ]

    def tags(self, user: Any) -> list[dict[str, Any]]:
        """The desk's own vocabulary. Staff only -- a tag is a note about a client."""
        _staff_only(user, "read the tag list")
        return [
            {"id": str(tag.pk), "name": tag.name, "slug": tag.slug, "colour": tag.colour}
            for tag in Tag.objects.all()
        ]

    def canned_replies(self, user: Any, *, category: str | None = None) -> list[dict[str, Any]]:
        """The things the desk says often. Staff only, and counted as they are fetched.

        The count is what tells a desk which of its saved replies are dead
        weight, and fetching is the closest observable moment to using one --
        the reply is inserted into a compose box client-side, and this app never
        learns whether it was sent.
        """
        _staff_only(user, "read the canned replies")
        replies = CannedReply.objects.filter(is_active=True).select_related("category")
        if category:
            replies = replies.filter(Q(category__slug=category) | Q(category__isnull=True))
        rows = list(replies)
        CannedReply.objects.filter(pk__in=[reply.pk for reply in rows]).update(
            used_count=F("used_count") + 1
        )
        return [
            {
                "id": str(reply.pk),
                "title": reply.title,
                "body": reply.body,
                "category": reply.category.slug if reply.category_id else None,
            }
            for reply in rows
        ]

    def stats(self, user: Any) -> dict[str, Any]:
        """The numbers a desk runs on. Staff only.

        One query for the counts and one for the averages, rather than a call
        per number: this is the screen an agent leaves open all day, and a
        dashboard that costs fifteen queries a refresh is a dashboard somebody
        turns off.
        """
        _staff_only(user, "read the desk statistics")
        counts = Ticket.objects.aggregate(
            total=Count("id"),
            open=Count("id", filter=Q(status=Status.OPEN)),
            pending=Count("id", filter=Q(status=Status.PENDING)),
            on_hold=Count("id", filter=Q(status=Status.ON_HOLD)),
            resolved=Count("id", filter=Q(status=Status.RESOLVED)),
            closed=Count("id", filter=Q(status=Status.CLOSED)),
            unassigned=Count("id", filter=Q(assignee__isnull=True, status__in=LIVE_STATUSES)),
            chats=Count("id", filter=Q(kind=Kind.CHAT)),
            rated=Count("id", filter=Q(rating__isnull=False)),
        )
        averages = Ticket.objects.aggregate(satisfaction=Avg("rating"))
        live = Ticket.objects.live().select_related("category")
        breached = sum(1 for ticket in live if ticket.breached)
        awaiting = live.filter(first_response_at__isnull=True).count()
        return {
            **counts,
            "breached": breached,
            "awaiting_first_response": awaiting,
            "satisfaction": round(averages["satisfaction"], 2)
            if averages["satisfaction"] is not None
            else None,
        }

    # -- opening and talking ----------------------------------------------

    def open(
        self,
        user: Any,
        *,
        subject: str = "",
        body: str = "",
        kind: str = str(Kind.TICKET),
        category: str | None = None,
        priority: str = "",
        upload_ids: list[UUID] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Open a thread as its client, with the first message already in it.

        The first message is part of opening rather than a second call, because
        a thread with nothing in it is not a question anybody can answer, and a
        client whose second call failed would have left one behind. A chat is
        the exception -- a widget opens the connection before the visitor has
        typed anything -- so a body is required for a ticket and optional for a
        chat.

        Opening on somebody else's behalf is not possible here, and that is
        deliberate: an agent creating a ticket "for" a client would be putting
        words in their mouth in a thread the client can read. The admin is where
        that belongs, where it is visibly an administrative act.
        """
        kind = _choice(kind, Kind, "kind")
        if priority:
            priority = _choice(priority, Priority, "priority")
        chosen = None
        if category:
            chosen = Category.objects.filter(slug=category, is_active=True).first()
            if chosen is None:
                raise InvalidRequest(f"There is no open category called {category!r}.")
        uploads = self._claim(user, upload_ids or [])
        if kind == Kind.TICKET:
            body = _body(body, uploads=len(uploads))
            if not subject.strip():
                raise InvalidRequest("A ticket needs a subject.")
        ticket = create_ticket(
            user,
            kind=kind,
            subject=subject,
            category=chosen,
            priority=priority,
            data=data or {},
        )
        if body.strip() or uploads:
            post_message(ticket, user, body.strip(), uploads=uploads)
            ticket.refresh_from_db()
        return self.ticket(user, ticket.pk)

    def send(
        self,
        user: Any,
        ticket_id: UUID,
        body: str,
        *,
        upload_ids: list[UUID] | None = None,
        internal: bool = False,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Say something in a thread.

        A closed thread is refused rather than reopened. Closing is the one
        final state in this app, and a message that silently reopened it would
        mean a desk could never be sure what its queue was: :meth:`reopen` is
        the explicit way, and it is available to the client.

        ``internal`` is staff-only and turns the message into a note. It is a
        flag on this method rather than a separate one so that the socket, where
        an agent is doing both in the same conversation, has one command.
        """
        ticket = self._ticket(user, ticket_id)
        if ticket.status == Status.CLOSED:
            raise NotPermitted("This ticket is closed. Reopen it before adding to it.")
        if internal:
            _staff_only(user, "leave an internal note")
        uploads = self._claim(user, upload_ids or [])
        text = _body(body, uploads=len(uploads))
        staff = getattr(user, "is_staff", False)
        join(ticket, user, role=str(Role.AGENT) if staff else str(Role.CLIENT))
        message = post_message(
            ticket,
            user,
            text,
            kind=str(MessageKind.NOTE) if internal else str(MessageKind.REPLY),
            visibility=str(Visibility.INTERNAL) if internal else str(Visibility.PUBLIC),
            data=data or {},
            uploads=uploads,
        )
        # Sending is also reading: whatever was in the thread when you replied
        # to it, you have seen. Doing it here rather than making the client send
        # a second command is what stops a badge surviving a conversation.
        mark_read(ticket, user, at=message.created_at)
        return events.message_payload(message)

    def edit(self, user: Any, message_id: UUID, body: str) -> dict[str, Any]:
        """Rewrite your own message. Never anybody else's -- see the model."""
        message = self._message(user, message_id)
        if not message.editable_by(user):
            raise NotPermitted("You can only edit your own messages, and not once they are gone.")
        return events.message_payload(edit_message(message, _body(body, uploads=1)))

    def delete(self, user: Any, message_id: UUID) -> dict[str, Any]:
        """Retract a message. Leaves a tombstone; never removes the row."""
        message = self._message(user, message_id)
        if not message.deletable_by(user):
            raise NotPermitted("You cannot delete that message.")
        return events.message_payload(delete_message(message))

    # -- state ------------------------------------------------------------

    def read(self, user: Any, ticket_id: UUID) -> dict[str, Any]:
        """Mark a thread read up to now, and tell the other side you did.

        Idempotent, and never moves the watermark backwards -- so a client that
        fires this from two tabs cannot un-read its own progress.
        """
        ticket = self._ticket(user, ticket_id)
        changed = mark_read(ticket, user)
        participant = ticket.participant_for(user)
        if changed:
            events.publish_read(ticket, user, participant.last_read_at if participant else None)
        return {
            "ticket": str(ticket.pk),
            "changed": changed,
            "unread": unread_count(ticket, user),
            "last_read_at": participant.last_read_at.isoformat()
            if participant and participant.last_read_at
            else None,
        }

    def unread_ticket(self, user: Any, ticket_id: UUID) -> dict[str, Any]:
        """Put a whole thread back in the badge. The undo for :meth:`read`."""
        ticket = self._ticket(user, ticket_id)
        changed = mark_unread(ticket, user)
        if changed:
            events.publish_read(ticket, user, None)
        return {
            "ticket": str(ticket.pk),
            "changed": changed,
            "unread": unread_count(ticket, user),
            "last_read_at": None,
        }

    def status(self, user: Any, ticket_id: UUID, status: str) -> dict[str, Any]:
        """Move a thread, and write the move into the thread as an event.

        A client may only settle or revive their own thread -- resolve, close,
        reopen. ``pending`` and ``on_hold`` are statements about what the desk is
        doing and are the desk's to make; a client setting "waiting on the
        client" about themselves is not a thing that means anything.
        """
        status = _choice(status, Status, "status")
        ticket = self._ticket(user, ticket_id)
        if not getattr(user, "is_staff", False):
            if ticket.client_id != user.pk:
                raise NotPermitted("Only the client who opened a ticket can settle it.")
            if status in (Status.PENDING, Status.ON_HOLD):
                raise NotPermitted("Only the support desk can set that status.")
        was = ticket.status
        changed = set_status(ticket, status, by=user)
        if changed:
            self._event(
                ticket,
                user,
                f"changed the status from {was} to {status}",
                {"field": "status", "from": was, "to": status},
            )
        return {"ticket": str(ticket.pk), "status": ticket.status, "changed": changed}

    def close(self, user: Any, ticket_id: UUID) -> dict[str, Any]:
        """Settle a thread for good. Both sides may; only :meth:`reopen` undoes it."""
        return self.status(user, ticket_id, str(Status.CLOSED))

    def reopen(self, user: Any, ticket_id: UUID) -> dict[str, Any]:
        """Revive a settled thread. The client's right of reply to being closed."""
        return self.status(user, ticket_id, str(Status.OPEN))

    def assign(self, user: Any, ticket_id: UUID, agent_id: UUID | None) -> dict[str, Any]:
        """Give a thread to an agent, or return it to the unassigned queue.

        The event is internal. Who a complaint has been passed between is the
        desk's business and telling the client would answer a question they did
        not ask with something that reads as an apology.
        """
        _staff_only(user, "assign a ticket")
        ticket = self._ticket(user, ticket_id)
        agent = None
        if agent_id is not None:
            agent = type(user).objects.filter(pk=agent_id, is_staff=True, is_active=True).first()
            if agent is None:
                raise InvalidRequest("That account is not an active member of staff.")
        was = ticket.assignee
        changed = assign(ticket, agent)
        if changed:
            self._event(
                ticket,
                user,
                f"assigned this to {agent.get_username() if agent else 'nobody'}",
                {
                    "field": "assignee",
                    "from": was.get_username() if was else None,
                    "to": agent.get_username() if agent else None,
                },
                internal=True,
            )
        return {
            "ticket": str(ticket.pk),
            "assignee": events.author_payload(agent),
            "changed": changed,
        }

    def claim(self, user: Any, ticket_id: UUID) -> dict[str, Any]:
        """Take an unassigned thread yourself. The one-click version of assigning.

        Refused when somebody else already has it, rather than silently taking
        it: two agents racing for the same ticket should both find out, and the
        loser should find out before they have typed an answer.
        """
        _staff_only(user, "claim a ticket")
        ticket = self._ticket(user, ticket_id)
        if ticket.assignee_id not in (None, user.pk):
            raise NotPermitted(f"{ticket.assignee.get_username()} already has this ticket.")
        return self.assign(user, ticket_id, user.pk)

    def priority(self, user: Any, ticket_id: UUID, priority: str) -> dict[str, Any]:
        """Move a thread up or down the queue. Staff only, and recorded internally."""
        _staff_only(user, "change a priority")
        priority = _choice(priority, Priority, "priority")
        ticket = self._ticket(user, ticket_id)
        was = ticket.priority
        changed = set_priority(ticket, priority)
        if changed:
            self._event(
                ticket,
                user,
                f"changed the priority from {was} to {priority}",
                {"field": "priority", "from": was, "to": priority},
                internal=True,
            )
        return {"ticket": str(ticket.pk), "priority": ticket.priority, "changed": changed}

    def tag(self, user: Any, ticket_id: UUID, slugs: list[str]) -> dict[str, Any]:
        """Replace a thread's tags with exactly these. Staff only.

        A replacement rather than an add, because that is what a tag picker
        does: the client sends the set it is now showing, and does not have to
        work out the difference from what it was showing before.
        """
        _staff_only(user, "tag a ticket")
        ticket = self._ticket(user, ticket_id)
        found = list(Tag.objects.filter(slug__in=slugs))
        missing = sorted(set(slugs) - {tag.slug for tag in found})
        if missing:
            raise InvalidRequest(f"No such tag: {', '.join(missing)}.")
        was = sorted(tag.slug for tag in ticket.tags.all())
        now = sorted(tag.slug for tag in found)
        ticket.tags.set(found)
        if was != now:
            self._event(
                ticket,
                user,
                "changed the tags",
                {"field": "tags", "from": was, "to": now},
                internal=True,
            )
        return {"ticket": str(ticket.pk), "tags": now, "changed": was != now}

    def invite(
        self, user: Any, ticket_id: UUID, account_id: UUID, role: str = str(Role.OBSERVER)
    ) -> dict[str, Any]:
        """Add somebody else to a thread. Staff only.

        Used for a second agent, or for the colleague of a client who is also
        affected. The role decides what they see -- an observer who is not staff
        reads the public half, exactly as the client does.
        """
        _staff_only(user, "add somebody to a ticket")
        role = _choice(role, Role, "role")
        ticket = self._ticket(user, ticket_id)
        account = type(user).objects.filter(pk=account_id, is_active=True).first()
        if account is None:
            raise InvalidRequest("No such active account.")
        participant = join(ticket, account, role=role)
        # The thread they have just been added to, on their own channel: they are
        # not subscribed to its channel yet and would otherwise not learn of it
        # until they next listed their tickets.
        events.publish_ticket(ticket, reason="invited")
        return events.participant_payload(participant)

    def rate(self, user: Any, ticket_id: UUID, score: int, comment: str = "") -> dict[str, Any]:
        """Say what you thought. The client's, and only once it is settled.

        Not staff's: an agent rating their own handling of a ticket is a number
        that means nothing. Overwriting is allowed, because an opinion formed
        the minute a ticket closed is allowed to change.
        """
        ticket = self._ticket(user, ticket_id)
        if ticket.client_id != getattr(user, "pk", None):
            raise NotPermitted("Only the client who opened a ticket can rate it.")
        if not ticket.is_settled:
            raise NotPermitted("Rate a ticket once it has been resolved or closed.")
        if not isinstance(score, int) or isinstance(score, bool) or not 1 <= score <= 5:
            raise InvalidRequest("A rating is a whole number of stars, one to five.")
        rate(ticket, score, comment.strip())
        self._event(
            ticket,
            user,
            f"rated this {score} out of 5",
            {"field": "rating", "to": score},
            internal=True,
        )
        return {"ticket": str(ticket.pk), "rating": score, "comment": comment.strip()}

    # -- attachments ------------------------------------------------------

    def stage_upload(self, user: Any, upload: Any) -> dict[str, Any]:
        """Store one file and hand back the id a message can claim it by.

        The two-step exists because a WebSocket frame cannot carry a multipart
        body -- see :class:`apps.support.models.Upload`. This is the half that
        has to be HTTP; the other half works over any of the four transports.
        """
        from apps.support.uploads import store

        staged = store(user, upload)
        return {
            "id": str(staged.pk),
            "name": staged.name,
            "url": staged.url,
            "content_type": staged.content_type,
            "size": staged.size,
        }

    def _claim(self, user: Any, upload_ids: list[UUID]) -> list[Upload]:
        """Turn upload ids into the rows they name, refusing anything not yours.

        Ownership *and* unclaimed, in one query. Naming somebody else's upload
        id and naming one already attached to a message are the same refusal,
        because the useful thing to know is that this id is not available to
        you, and the difference between the two reasons tells you about a file
        you cannot see.
        """
        if not upload_ids:
            return []
        found = list(Upload.objects.filter(pk__in=upload_ids, owner=user, attachment__isnull=True))
        if len(found) != len(set(upload_ids)):
            raise InvalidRequest("One of those uploads does not exist, or is already attached.")
        return found

    # -- ephemeral --------------------------------------------------------

    def typing(self, user: Any, ticket_id: UUID, typing: bool = True) -> dict[str, Any]:
        """Tell the thread somebody is typing. Never stored -- see :mod:`apps.support.events`."""
        ticket = self._ticket(user, ticket_id)
        events.publish_typing(ticket.pk, user, typing=typing)
        return {"ticket": str(ticket.pk), "typing": typing}

    def presence(self, user: Any, ticket_id: UUID, present: bool = True) -> dict[str, Any]:
        """Tell the thread somebody has it open, or has left it."""
        ticket = self._ticket(user, ticket_id)
        events.publish_presence(ticket.pk, user, present=present)
        return {"ticket": str(ticket.pk), "present": present}

    # -- writing the history ----------------------------------------------

    def _event(
        self,
        ticket: Ticket,
        actor: Any,
        sentence: str,
        data: dict[str, Any],
        *,
        internal: bool = False,
    ) -> Message:
        """Record a change in the thread it happened to.

        Events are messages -- see the models docstring -- so the history a
        client reads and the history the desk audits are one list that cannot
        disagree. The sentence is English for a log and for a client with no
        translation table; ``data`` is the machine-readable version of the same
        thing, which is what a client should actually render.
        """
        return post_message(
            ticket,
            actor,
            sentence,
            kind=str(MessageKind.EVENT),
            visibility=str(Visibility.INTERNAL) if internal else str(Visibility.PUBLIC),
            data={"event": data.get("field", "change"), **data},
        )


support_service = SupportService()
