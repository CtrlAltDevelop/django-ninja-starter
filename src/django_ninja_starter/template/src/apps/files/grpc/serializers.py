"""The nested message the file service answers with."""

from rest_framework import serializers


class StoredFile(serializers.Serializer[dict[str, object]]):
    """One stored file's metadata."""

    id = serializers.CharField()
    owner_id = serializers.CharField()
    name = serializers.CharField()
    content_type = serializers.CharField()
    size = serializers.IntegerField()
    created_at = serializers.CharField()
