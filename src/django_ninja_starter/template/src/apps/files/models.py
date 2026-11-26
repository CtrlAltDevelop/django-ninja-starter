"""A file somebody uploaded, and who it belongs to.

The bytes live in Django's configured storage (local disk by default, S3 when
``DJANGO_STORAGE_BACKEND=s3``); the row holds the key it was saved under, the
name the uploader gave it, and what its content was sniffed to be.
"""

import uuid
from typing import Any

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver


class StoredFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="stored_files"
    )
    name = models.CharField(max_length=255, help_text="The name the uploader gave the file.")
    storage_key = models.CharField(
        max_length=255,
        unique=True,
        editable=False,
        help_text="Where the bytes live in the configured storage. Random, never the upload name.",
    )
    content_type = models.CharField(
        max_length=120, help_text="What the content was sniffed to be, not what the client said."
    )
    size = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = "file"

    def __str__(self) -> str:
        return self.name


@receiver(post_delete, sender=StoredFile)
def _delete_bytes(sender: Any, instance: StoredFile, **kwargs: Any) -> None:
    """Delete the bytes with the row, however the row went (service, admin, cascade)."""
    default_storage.delete(instance.storage_key)
