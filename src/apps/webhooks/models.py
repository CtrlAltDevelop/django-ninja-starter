"""Outgoing webhooks: where to send, what was sent, and every try at sending it."""

import uuid

from django.conf import settings
from django.db import models


class Endpoint(models.Model):
    """An https URL an account wants told about some of its events."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="webhook_endpoints"
    )
    url = models.URLField(max_length=2000)
    events = models.JSONField(default=list, help_text="The event types this endpoint receives.")
    secret = models.CharField(
        max_length=64, help_text="The HMAC-SHA256 key deliveries are signed with."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.url


class DeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"


class Delivery(models.Model):
    """One event bound for one endpoint, retried until it lands or gives up."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    endpoint = models.ForeignKey(Endpoint, on_delete=models.CASCADE, related_name="deliveries")
    event = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name_plural = "deliveries"

    def __str__(self) -> str:
        return f"{self.event} -> {self.endpoint_id}"


class DeliveryAttempt(models.Model):
    """What happened the one time a delivery was tried. A record, never edited."""

    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="attempt_log")
    number = models.PositiveIntegerField()
    status_code = models.PositiveIntegerField(null=True, blank=True)
    error = models.TextField(blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("number",)

    def __str__(self) -> str:
        return f"{self.delivery_id} #{self.number}"
