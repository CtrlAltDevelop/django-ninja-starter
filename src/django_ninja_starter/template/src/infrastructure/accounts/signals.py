"""Guarantee a profile exists, whichever direction the account arrived from.

Accounts are created from a lot of places: four login methods, four social
callbacks, the admin, ``createsuperuser``, and whatever a project adds. Creating
the profile in the manager would cover the first few and miss the rest, so it
happens on the signal that all of them raise.

The result is a promise the rest of the code can lean on: ``user.profile`` is
always there, and nothing has to defend against its absence.
"""

from typing import Any

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from infrastructure.accounts.models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL, dispatch_uid="accounts.create_profile")
def create_profile(sender: Any, instance: Any, created: bool, **kwargs: Any) -> None:
    """Give every new account a profile, seeded with the project's defaults."""
    if kwargs.get("raw") or not created:
        # `raw` means loaddata is replaying a fixture, which carries its own
        # profile rows. Creating one here would collide with the row about to
        # be loaded.
        return
    Profile.objects.get_or_create(
        user=instance,
        defaults={
            "locale": settings.ACCOUNTS_DEFAULT_LOCALE,
            "timezone": settings.ACCOUNTS_DEFAULT_TIMEZONE,
        },
    )
