"""The nested messages the notes service answers with.

A flat message is declared as a list of field dictionaries; a nested one has to
be a type protoc can name, and django-socio-grpc builds those from serializers.
Named for the message rather than for the class, because a nested field takes
the class name verbatim.
"""

from rest_framework import serializers


class Note(serializers.Serializer[dict[str, object]]):
    """A whole note, as it appears inside a ``NoteList``."""

    id = serializers.CharField()
    title = serializers.CharField()
    body = serializers.CharField()
    pinned = serializers.BooleanField()
    created_at = serializers.CharField()
    updated_at = serializers.CharField()


class NoteSummary(serializers.Serializer[dict[str, object]]):
    """Enough to render a row, without the whole body."""

    id = serializers.CharField()
    title = serializers.CharField()
    excerpt = serializers.CharField()
    pinned = serializers.BooleanField()
    updated_at = serializers.CharField()
