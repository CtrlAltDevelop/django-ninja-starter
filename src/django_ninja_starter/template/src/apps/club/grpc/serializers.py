"""The nested messages the club service answers with.

``criteria``, ``metadata`` and an event's ``fields`` travel as JSON strings for
the reason they do on every other transport here: their shape belongs to whoever
wrote the mission or registered the event, and there is no protobuf type that
fits every one of them.

Timestamps travel as strings rather than ``Timestamp`` messages to match what the
REST document carries, so a client reading both sees one format.
"""

from rest_framework import serializers


class Level(serializers.Serializer[dict[str, object]]):
    """One rung of a ladder."""

    id = serializers.CharField()
    position = serializers.IntegerField()
    name = serializers.CharField()
    xp_required = serializers.IntegerField()
    logo = serializers.CharField()
    perks = serializers.CharField()
    metadata = serializers.JSONField()


class ClubBrief(serializers.Serializer[dict[str, object]]):
    id = serializers.CharField()
    name = serializers.CharField()
    slug = serializers.CharField()
    logo = serializers.CharField()


class Club(serializers.Serializer[dict[str, object]]):
    """One club. ``levels`` is filled in when it was asked for by name."""

    id = serializers.CharField()
    name = serializers.CharField()
    slug = serializers.CharField()
    description = serializers.CharField()
    logo = serializers.CharField()
    status = serializers.CharField()
    join_policy = serializers.CharField()
    is_open = serializers.BooleanField()
    member_count = serializers.IntegerField()
    metadata = serializers.JSONField()
    created_at = serializers.CharField()
    levels = Level(many=True)


class Membership(serializers.Serializer[dict[str, object]]):
    """Where one account stands.

    ``xp`` is the sum of an immutable award ledger rather than a stored counter,
    so it is always the number the awards add up to.
    """

    id = serializers.CharField()
    club = ClubBrief()
    status = serializers.CharField()
    xp = serializers.IntegerField()
    level = Level(required=False)
    next_level = Level(required=False)
    xp_to_next = serializers.IntegerField()
    progress = serializers.FloatField()
    joined_at = serializers.CharField()


class Mission(serializers.Serializer[dict[str, object]]):
    """One mission, and how far the caller has got with it."""

    id = serializers.CharField()
    code = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField()
    event = serializers.CharField()
    criteria = serializers.JSONField()
    xp = serializers.IntegerField()
    repeat = serializers.CharField()
    target_count = serializers.IntegerField()
    count = serializers.IntegerField()
    completions = serializers.IntegerField()
    completed = serializers.BooleanField()
    available = serializers.BooleanField()
    last_completed_at = serializers.CharField(allow_blank=True)
    starts_at = serializers.CharField(allow_blank=True)
    ends_at = serializers.CharField(allow_blank=True)


class Award(serializers.Serializer[dict[str, object]]):
    """One payment of XP. Immutable, and what a level is derived from."""

    id = serializers.CharField()
    xp = serializers.IntegerField()
    reason = serializers.CharField(allow_blank=True)
    reference = serializers.CharField()
    mission_id = serializers.CharField(allow_blank=True)
    metadata = serializers.JSONField()
    created_at = serializers.CharField()


class Rank(serializers.Serializer[dict[str, object]]):
    """One row of a club's leaderboard."""

    position = serializers.IntegerField()
    membership_id = serializers.CharField()
    username = serializers.CharField()
    xp = serializers.IntegerField()
    level = Level(required=False)
    is_you = serializers.BooleanField()


class Event(serializers.Serializer[dict[str, object]]):
    """One thing this deployment can build a mission out of."""

    key = serializers.CharField()
    label = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    value_label = serializers.CharField(allow_blank=True)
    fields = serializers.JSONField()
    source = serializers.CharField(allow_blank=True)
