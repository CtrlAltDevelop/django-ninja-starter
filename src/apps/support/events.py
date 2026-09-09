"""Turning a saved row into something the sockets can be handed.

Broadcasting is wired to ``post_save`` rather than done at each call site, for
the same reason the response envelope is applied by the renderer rather than by
each view: there is no path that posts a message and forgets to deliver it. The
admin, a management command, a shell session, the REST endpoints, the GraphQL
mutations, the gRPC calls and the socket all reach the other side of the
conversation by the one route.

It is deferred to ``on_commit``, so nothing is pushed to a client for a row that
a later exception rolls back. An agent told about a message that then failed to
save would answer something nobody said.

**Confidentiality is enforced on the way out of the socket, not on the way into
the broker.** An internal note is published to the thread's channel like any
other message, carrying its ``visibility``, and each connection drops what its
own account may not read -- see :meth:`apps.support.sockets.SupportSocket._is_for_us`.
The alternative is a second channel per thread for staff traffic, which doubles
the subscriptions every agent holds and puts the rule in the one place nothing
can test end to end. Here it sits next to the account it is being applied for.

**Every frame has a ``type``.** A socket that only ever received one shape of
message would force every future addition to be smuggled inside a field of the
payload, and this app has seven kinds from the start: a message, a thread
whose state moved, a typing indicator, a presence change, a read watermark, a
badge, and an error.
"""

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.support.broadcast import get_broker, staff_channel, ticket_channel, user_channel
from apps.support.models import (
    Message,
    MessageKind,
    Participant,
    Ticket,
    Visibility,
    unread_count,
)

MESSAGE_FRAME = "message"
TICKET_FRAME = "ticket"
TYPING_FRAME = "typing"
PRESENCE_FRAME = "presence"
READ_FRAME = "read"


# -- what a client is handed ----------------------------------------------
#
# One function per row, used by every transport. A client that had to reconcile
# the socket's shape of a message with the REST endpoint's shape of the same
# message would write the reconciliation itself, and would get it wrong for
# exactly the fields nobody thought to test.


def author_payload(user: Any | None) -> dict[str, Any] | None:
    """Who said something, or ``None`` for the system.

    Deliberately not the whole account. A client is entitled to know that an
    agent answered and what to call them; it is not entitled to the agent's
    email address, and an agent is not entitled to a colleague's either.
    """
    if user is None:
        return None
    return {
        "id": str(user.pk),
        "username": user.get_username(),
        "staff": bool(getattr(user, "is_staff", False)),
    }


def attachment_payload(attachment: Any) -> dict[str, Any]:
    return {
        "id": str(attachment.pk),
        "name": attachment.name,
        "url": attachment.url,
        "content_type": attachment.content_type,
        "size": attachment.size,
    }


def message_payload(message: Message) -> dict[str, Any]:
    """One message as a client receives it, over the socket or over HTTP.

    A deleted message keeps its identity and loses its body. It is sent rather
        than withheld because two people are reading the thread and one of them
    has it on screen: a row that simply vanished would leave a message that
    cannot be marked read, replied to or explained. So the tombstone travels,
    and a client renders "this message was deleted".
    """
    deleted = message.deleted_at is not None
    return {
        "id": str(message.pk),
        "ticket": str(message.ticket_id),
        "author": author_payload(message.author),
        "kind": message.kind,
        "visibility": message.visibility,
        "body": "" if deleted else message.body,
        "data": {} if deleted else message.data,
        "attachments": []
        if deleted
        else [attachment_payload(item) for item in message.attachments.all()],
        "created_at": message.created_at.isoformat(),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "deleted": deleted,
    }


def sla_payload(ticket: Ticket) -> dict[str, Any]:
    """The two promises and whether either was missed.

    The breach flags are computed against the clock every time this is called
    rather than stored, so a thread that goes over its deadline while nobody is
    looking is in breach the moment somebody looks -- with no job having had to
    run. See :class:`apps.support.models.Ticket`.
    """
    return {
        "first_response_due_at": ticket.first_response_due_at.isoformat()
        if ticket.first_response_due_at
        else None,
        "resolution_due_at": ticket.resolution_due_at.isoformat()
        if ticket.resolution_due_at
        else None,
        "first_response_at": ticket.first_response_at.isoformat()
        if ticket.first_response_at
        else None,
        "first_response_breached": ticket.first_response_breached,
        "resolution_breached": ticket.resolution_breached,
        "breached": ticket.breached,
    }


def ticket_payload(ticket: Ticket, *, unread: int | None = None) -> dict[str, Any]:
    """One thread as a client receives it, without its messages.

    ``unread`` is per account and therefore an argument rather than a field:
    the same thread has a different value for the client and for each agent in
    it, and both are correct. Left out where nobody in particular is being
    addressed -- a frame on the desk channel announcing a new ticket has no
    single reader to count for.
    """
    payload: dict[str, Any] = {
        "id": str(ticket.pk),
        "reference": ticket.reference,
        "kind": ticket.kind,
        "subject": ticket.subject,
        "status": ticket.status,
        "priority": ticket.priority,
        "client": author_payload(ticket.client),
        "assignee": author_payload(ticket.assignee),
        "category": {
            "id": str(ticket.category_id),
            "name": ticket.category.name,
            "slug": ticket.category.slug,
        }
        if ticket.category_id
        else None,
        "tags": [tag.slug for tag in ticket.tags.all()],
        "data": ticket.data,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
        "last_message_at": ticket.last_message_at.isoformat() if ticket.last_message_at else None,
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else None,
        "rating": ticket.rating,
        "rating_comment": ticket.rating_comment,
        "sla": sla_payload(ticket),
    }
    if unread is not None:
        payload["unread"] = unread
    return payload


def participant_payload(participant: Participant) -> dict[str, Any]:
    return {
        "user": author_payload(participant.user),
        "role": participant.role,
        "joined_at": participant.joined_at.isoformat(),
        "last_read_at": participant.last_read_at.isoformat() if participant.last_read_at else None,
        "notify": participant.notify,
    }


# -- getting it there ------------------------------------------------------


def _after_commit(channel: str, frame: dict[str, Any]) -> None:
    broker = get_broker()
    transaction.on_commit(lambda: broker.publish(channel, frame))


def _publish_now(channel: str, frame: dict[str, Any]) -> None:
    """Publish without waiting for a commit, for something that was never a row.

    A typing indicator and a presence change have nothing to roll back and are
    worthless a second late, so they do not go through ``on_commit`` -- which in
    a request with an open transaction would hold them until it closed.
    """
    get_broker().publish(channel, frame)


def publish_message(message: Message) -> None:
    """Push a message to everybody in its thread, and a badge to each of them.

    Two frames rather than one. The message goes to the thread's channel, which
    is where a client with the conversation open is listening. The badge goes to
    each participant's own channel, because a client looking at a *different*
    thread -- or at no thread at all -- still has to know its unread count moved,
    and is not subscribed to this thread to hear it.
    """
    frame = {
        "type": MESSAGE_FRAME,
        "ticket": str(message.ticket_id),
        "message": message_payload(message),
    }
    _after_commit(ticket_channel(message.ticket_id), frame)
    _publish_badges(message.ticket, exclude=message.author_id)


def _publish_badges(ticket: Ticket, *, exclude: Any = None) -> None:
    """Tell each participant their unread count for this thread moved.

    Counted per person, because it is a different number for each of them: the
    client cannot see the internal notes and nobody's own messages count as
    unread. Whoever caused the change is skipped -- they have the message
    already, and telling them they have not read what they just wrote is the one
    badge update that is always wrong.
    """
    for participant in ticket.participants.select_related("user"):
        if exclude is not None and participant.user_id == exclude:
            continue
        if not participant.notify:
            continue
        frame = {
            "type": TICKET_FRAME,
            "ticket": ticket_payload(ticket, unread=unread_count(ticket, participant.user)),
            "reason": "message",
        }
        _after_commit(user_channel(participant.user_id), frame)


def publish_ticket(ticket: Ticket, *, reason: str = "update", to_desk: bool = False) -> None:
    """Push a thread's current state to the thread, and optionally to the desk.

    ``to_desk`` is for the one case the thread channel cannot serve: a ticket
    that has just been opened has to reach agents who are not in it yet, and by
    definition none of them is subscribed to it.
    """
    frame = {"type": TICKET_FRAME, "ticket": ticket_payload(ticket), "reason": reason}
    _after_commit(ticket_channel(ticket.pk), frame)
    if to_desk:
        _after_commit(staff_channel(), frame)


def publish_typing(ticket_id: Any, user: Any, *, typing: bool) -> None:
    """Say that somebody is -- or has stopped -- typing in a thread.

    Never stored. A typing indicator is true for three seconds and a row that
    outlives its own truth is worse than no row: a client reconnecting would be
    told an agent is typing something they finished yesterday. So this is the
    one thing in the app that exists only as a frame.
    """
    _publish_now(
        ticket_channel(ticket_id),
        {
            "type": TYPING_FRAME,
            "ticket": str(ticket_id),
            "user": author_payload(user),
            "typing": typing,
        },
    )


def publish_presence(ticket_id: Any, user: Any, *, present: bool) -> None:
    """Say that somebody has opened or left a thread. Ephemeral, like typing."""
    _publish_now(
        ticket_channel(ticket_id),
        {
            "type": PRESENCE_FRAME,
            "ticket": str(ticket_id),
            "user": author_payload(user),
            "present": present,
        },
    )


def publish_read(ticket: Ticket, user: Any, at: Any) -> None:
    """Tell the thread that somebody has read up to a point, and them their new badge.

    The thread frame is what draws the other side's read ticks. The account
    frame is what clears the badge on this person's *other* devices, so a
    conversation opened on a phone stops being bold on the laptop.
    """
    frame = {
        "type": READ_FRAME,
        "ticket": str(ticket.pk),
        "user": author_payload(user),
        "last_read_at": at.isoformat() if at else None,
    }
    _after_commit(ticket_channel(ticket.pk), frame)
    _after_commit(
        user_channel(user.pk),
        {
            "type": TICKET_FRAME,
            "ticket": ticket_payload(ticket, unread=unread_count(ticket, user)),
            "reason": "read",
        },
    )


# -- signals ---------------------------------------------------------------


@receiver(post_save, sender=Message, dispatch_uid="support.message.broadcast")
def _broadcast_message(
    sender: type[Message], instance: Message, created: bool, **kwargs: object
) -> None:
    """Deliver on creation, and on an edit or a retraction.

    Unlike a notification, a message *is* re-sent when it changes: an edited
    message and a deleted one are both facts the people reading the thread have
    to be told, and the frame carries ``edited_at`` and ``deleted`` so a client
    can tell which happened. What is not re-sent is the badge -- editing your
    own sentence does not make it unread again -- which is why
    :func:`_publish_badges` is only reached through the creation path.
    """
    if created:
        publish_message(instance)
        _notify_counterparts(instance)
        return
    frame = {
        "type": MESSAGE_FRAME,
        "ticket": str(instance.ticket_id),
        "message": message_payload(instance),
    }
    _after_commit(ticket_channel(instance.ticket_id), frame)


@receiver(post_save, sender=Ticket, dispatch_uid="support.ticket.broadcast")
def _broadcast_ticket(
    sender: type[Ticket], instance: Ticket, created: bool, **kwargs: object
) -> None:
    """Announce a new thread to the desk, and any change to whoever is in it."""
    publish_ticket(instance, reason="opened" if created else "update", to_desk=created)


def connect() -> None:
    """Called from ``AppConfig.ready``. Importing this module is what registers the
    receivers; this exists so that the import is deliberate rather than incidental."""


# -- telling somebody who is not connected --------------------------------


def _notify_counterparts(message: Message) -> None:
    """Raise a notification for the people a new message is waiting on.

    The socket reaches whoever is connected; this reaches whoever is not, and it
    is the whole reason a support app needs a notification app beside it -- an
    agent's reply that is only ever a WebSocket frame is a reply the client
    never sees if they closed the tab.

    Optional in both directions. The notifications app is an opt-in feature of
    this project, so this checks whether it is installed rather than importing
    it at module level, and does nothing at all when it is not. A project that
    wants a different channel -- email, push -- replaces this one function.
    """
    if not apps.is_installed("apps.notifications"):
        return
    if message.kind == MessageKind.EVENT:
        return
    from apps.notifications.events import notify_users

    recipients = list(_recipients(message))
    if not recipients:
        return
    ticket = message.ticket
    label = ticket.subject or ticket.reference
    notify_users(
        recipients,
        f"New message on {label}",
        body=message.body[:280],
        link=f"/support/tickets/{ticket.pk}",
        data={"ticket": str(ticket.pk), "message": str(message.pk), "reference": ticket.reference},
    )


def _recipients(message: Message) -> Iterable[Any]:
    """Everybody in the thread who should hear about this message but did not write it.

    An internal note goes to the desk side only, which is the same rule the
    socket applies to the same message -- said once here for the people who are
    not connected and once there for the people who are, because the two
    audiences are reached by different machinery and there is no single place
    that owns both.
    """
    internal = message.visibility == Visibility.INTERNAL
    for participant in message.ticket.participants.select_related("user"):
        if participant.user_id == message.author_id or not participant.notify:
            continue
        if internal and not getattr(participant.user, "is_staff", False):
            continue
        yield participant.user
