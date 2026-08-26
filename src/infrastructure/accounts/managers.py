"""Creating accounts, from wherever they are created.

Every login method eventually lands here: a password signup, a redeemed email
code, a followed magic link, a social callback, and ``createsuperuser`` all go
through one of these two methods. That is deliberate -- it is what lets the
normalisation, the unusable-password rule and the profile all be stated once.
"""

from typing import TYPE_CHECKING, Any

from django.contrib.auth.base_user import BaseUserManager

if TYPE_CHECKING:  # pragma: no cover
    from infrastructure.accounts.models import User


class UserManager(BaseUserManager["User"]):
    """The manager ``get_user_model()._default_manager`` hands back."""

    use_in_migrations = True

    def _create(
        self,
        username: str,
        email: str | None,
        password: str | None,
        **extra_fields: Any,
    ) -> "User":
        if not username:
            raise ValueError("A user needs a username.")
        user = self.model(
            username=username,
            email=self.normalize_email(email).lower() or None,
            **extra_fields,
        )
        if password:
            user.set_password(password)
        else:
            # Passwordless by construction: an account created by an emailed code
            # or a social callback has no password, and leaving the field at its
            # empty default would make it *look* like one that was never set.
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        **extra_fields: Any,
    ) -> "User":
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create(username, email, password, **extra_fields)

    def create_superuser(
        self,
        username: str,
        email: str | None = None,
        password: str | None = None,
        **extra_fields: Any,
    ) -> "User":
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if not extra_fields["is_staff"] or not extra_fields["is_superuser"]:
            raise ValueError("A superuser must have is_staff and is_superuser set.")
        return self._create(username, email, password, **extra_fields)
