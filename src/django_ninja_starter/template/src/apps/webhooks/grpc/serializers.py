"""The nested messages the webhook service answers with."""

from rest_framework import serializers


class WebhookEndpoint(serializers.Serializer[dict[str, object]]):
    id = serializers.CharField()
    url = serializers.CharField()
    events = serializers.ListField(child=serializers.CharField())
    is_active = serializers.BooleanField()
    created_at = serializers.CharField()
    secret = serializers.CharField()


class WebhookAttempt(serializers.Serializer[dict[str, object]]):
    number = serializers.IntegerField()
    status_code = serializers.IntegerField()
    error = serializers.CharField()
    duration_ms = serializers.IntegerField()
    created_at = serializers.CharField()


class WebhookDelivery(serializers.Serializer[dict[str, object]]):
    id = serializers.CharField()
    event = serializers.CharField()
    status = serializers.CharField()
    attempts = serializers.IntegerField()
    next_attempt_at = serializers.CharField()
    created_at = serializers.CharField()
    attempt_log = WebhookAttempt(many=True)
