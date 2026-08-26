"""The account every other app in this project points at.

A custom user model from the first migration, because Django's own advice is
that swapping one in later is among the most painful changes a project can make
-- and a starter that ships the stock model has already made that choice for
whoever uses it.

Two decisions are worth explaining, because both differ from the obvious.

**``username`` is the login field, not ``email``.** This project can create an
account from a phone number alone, and an email-only account model would have to
invent a fake address to do it. So the stable identifier is a username -- one the
user picks, or one derived for them -- and the address is a separate, optional,
*unique* field. Email login still works: the password method accepts an address
in its identifier box, and the code and link methods resolve one directly.

**``email`` is nullable rather than blank.** A unique column cannot hold two
empty strings, and a project with more than one phone-only account would collide
on the second. NULL is the honest way to say "this account has no address", and
the only shape a unique optional column can take.
"""

import uuid
from typing import Any, Final

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from infrastructure.accounts.managers import UserManager

USERNAME_MAX_LENGTH: Final = 150


class User(AbstractBaseUser, PermissionsMixin):
    """A person, or a machine, that this API can recognise."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(
        max_length=USERNAME_MAX_LENGTH,
        unique=True,
        help_text="How this account signs in. Derived automatically for passwordless signups.",
    )
    email = models.EmailField(
        unique=True,
        null=True,
        blank=True,
        help_text="Optional and unique. Absent for accounts created from a phone number.",
    )
    email_verified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when a code or link sent to this address was redeemed.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Unset instead of deleting. Every login path refuses an inactive account.",
    )
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "username"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        ordering = ["-date_joined"]
        indexes = [models.Index(fields=["is_active", "-date_joined"])]

    def __str__(self) -> str:
        return self.username

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    def mark_email_verified(self) -> None:
        """Record that a code or link sent to this address came back."""
        if self.email_verified_at is None:
            self.email_verified_at = timezone.now()
            self.save(update_fields=["email_verified_at", "updated_at"])

    def get_full_name(self) -> str:
        """The profile's display name, falling back to something always present."""
        profile = getattr(self, "profile", None)
        return (profile.display_name if profile else "") or self.username

    def get_short_name(self) -> str:
        return self.get_full_name().split(" ", 1)[0]


class Profile(models.Model):
    """Everything about an account that authentication has no opinion about.

    Split from :class:`User` rather than piled onto it, so the table every login
    reads on every request stays narrow, and so a project can add its own columns
    here without touching the model Django's auth machinery is wired to.

    Created automatically for every account, from whichever direction the account
    arrived -- see :mod:`infrastructure.accounts.signals`. Code may therefore
    assume ``user.profile`` exists.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
        primary_key=True,
    )
    display_name = models.CharField(max_length=150, blank=True)
    avatar_url = models.URLField(max_length=1000, blank=True)
    bio = models.TextField(blank=True)
    locale = models.CharField(max_length=35, blank=True)
    timezone = models.CharField(max_length=64, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    marketing_opt_in = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "profile"
        verbose_name_plural = "profiles"

    def __str__(self) -> str:
        return self.display_name or str(self.user_id)

    def fill_blanks(self, **values: Any) -> list[str]:
        """Set only the fields that are still empty, and report which changed.

        This is what a social login uses. A provider knowing a name is not a
        reason to overwrite the one somebody typed here, so a value already set
        always wins -- and signing in with Google a second time cannot quietly
        undo an edit made in between.
        """
        changed = []
        for field, value in values.items():
            if value and not getattr(self, field, None):
                setattr(self, field, value)
                changed.append(field)
        if changed:
            self.save(update_fields=[*changed, "updated_at"])
        return changed
