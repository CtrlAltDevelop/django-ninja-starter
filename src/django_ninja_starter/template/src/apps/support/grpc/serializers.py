"""The nested messages the support service answers with.

Two shapes here do not survive protobuf unchanged, and both are handled the way
the notifications and CMS apps handle the same problem.

``data`` is free-form -- whatever the client that opened the thread attached, or
what an event recorded -- so it travels as ``data_json`` and a client parses it
if it cares.

**A null timestamp travels as an empty string.** proto3 has presence for
message fields and not for scalars, so ``resolved_at`` on a thread that has not
been resolved would come back as ``""`` whether it was declared optional or not.
Declaring it a plain string and saying so once here is more honest than three
transports disagreeing about what absent looks like.
"""

from rest_framework import serializers


class Account(serializers.Serializer[dict[str, object]]):
    """Who somebody is, as the other side of a conversation may know them."""

    id = serializers.CharField()
    username = serializers.CharField()
    staff = serializers.BooleanField()


class Sla(serializers.Serializer[dict[str, object]]):
    """The two deadlines, and whether either was missed.

    The breach flags are computed when the call is answered rather than stored,
    so they are true the instant they are true.
    """

    first_response_due_at = serializers.CharField()
    resolution_due_at = serializers.CharField()
    first_response_at = serializers.CharField()
    first_response_breached = serializers.BooleanField()
    resolution_breached = serializers.BooleanField()
    breached = serializers.BooleanField()


class Attachment(serializers.Serializer[dict[str, object]]):
    id = serializers.CharField()
    name = serializers.CharField()
    url = serializers.CharField()
    content_type = serializers.CharField()
    size = serializers.IntegerField()


class Message(serializers.Serializer[dict[str, object]]):
    """One message, as the calling account may read it.

    ``author`` is absent for something the system said and for a deleted
    account, which protobuf can express because it is a message field.
    """

    id = serializers.CharField()
    ticket = serializers.CharField()
    author = Account()
    kind = serializers.CharField()
    visibility = serializers.CharField()
    body = serializers.CharField()
    data_json = serializers.CharField()
    attachments = Attachment(many=True)
    created_at = serializers.CharField()
    edited_at = serializers.CharField()
    deleted = serializers.BooleanField()


class Participant(serializers.Serializer[dict[str, object]]):
    user = Account()
    role = serializers.CharField()
    joined_at = serializers.CharField()
    last_read_at = serializers.CharField()
    notify = serializers.BooleanField()


class CategoryRef(serializers.Serializer[dict[str, object]]):
    id = serializers.CharField()
    name = serializers.CharField()
    slug = serializers.CharField()


class Ticket(serializers.Serializer[dict[str, object]]):
    """One conversation, without its messages.

    ``unread`` is per account rather than a column: the same thread has a
    different value for the client and for each agent, and both are right.
    """

    id = serializers.CharField()
    reference = serializers.CharField()
    kind = serializers.CharField()
    subject = serializers.CharField()
    slug = serializers.CharField()
    status = serializers.CharField()
    priority = serializers.CharField()
    client = Account()
    assignee = Account()
    category = CategoryRef()
    tags = serializers.ListField(child=serializers.CharField())
    data_json = serializers.CharField()
    created_at = serializers.CharField()
    updated_at = serializers.CharField()
    last_message_at = serializers.CharField()
    resolved_at = serializers.CharField()
    closed_at = serializers.CharField()
    rating = serializers.IntegerField()
    rating_comment = serializers.CharField()
    sla = Sla()
    unread = serializers.IntegerField()
    participants = Participant(many=True)


class Category(serializers.Serializer[dict[str, object]]):
    """What a ticket can be about, and what the desk promised about it."""

    id = serializers.CharField()
    name = serializers.CharField()
    slug = serializers.CharField()
    description = serializers.CharField()
    default_priority = serializers.CharField()
    first_response_minutes = serializers.IntegerField()
    resolution_minutes = serializers.IntegerField()


class Tag(serializers.Serializer[dict[str, object]]):
    id = serializers.CharField()
    name = serializers.CharField()
    slug = serializers.CharField()
    colour = serializers.CharField()


class CannedReply(serializers.Serializer[dict[str, object]]):
    id = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    category = serializers.CharField()


class Channel(Ticket):
    """A channel as the directory lists it: a thread, plus your relation to it."""

    joined = serializers.BooleanField()
    members = serializers.IntegerField()
