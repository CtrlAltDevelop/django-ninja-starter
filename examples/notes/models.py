"""A note, owned by exactly one account.

Two details are worth copying into any feature app that stores per-user rows.

**The owner is ``settings.AUTH_USER_MODEL``, never the model class.** This
project ships a custom user and supports swapping it, and a direct import would
nail the table to whichever model happened to be active when the migration ran.

**Ownership is a database constraint, not a convention.** Every query in the API
filters on the caller, and the index below is what keeps that filter cheap once
one account has thousands of rows.
"""

import uuid

from django.conf import settings
from django.db import models


class Note(models.Model):
    """Something a person wrote down and expects to find again."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notes",
    )
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    pinned = models.BooleanField(
        default=False,
        help_text="Pinned notes sort ahead of the rest, whatever their age.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-pinned", "-updated_at"]
        indexes = [models.Index(fields=["owner", "-pinned", "-updated_at"])]

    def __str__(self) -> str:
        return self.title
