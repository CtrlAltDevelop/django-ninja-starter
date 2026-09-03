"""The nested message the notification service answers with.

``data`` is free-form -- whatever the code that raised the notification attached
-- so it travels as ``data_json`` and a client parses it if it cares. Everything
else has a shape worth declaring.
"""

from rest_framework import serializers


class Notification(serializers.Serializer[dict[str, object]]):
    """One notification, as this account sees it."""

    id = serializers.CharField()
    audience = serializers.CharField()
    subject = serializers.CharField()
    body = serializers.CharField()
    level = serializers.CharField()
    link = serializers.CharField()
    data_json = serializers.CharField()
    created_at = serializers.CharField()
    read = serializers.BooleanField()
    dismissed = serializers.BooleanField()
