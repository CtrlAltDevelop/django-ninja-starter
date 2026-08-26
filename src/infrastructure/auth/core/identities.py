"""Normalise what a user types and map it to a local account.

Login methods differ in what they accept -- an email, a phone number, a username
-- but all of them need the same two guarantees: the value is canonical before it
is compared or stored, and one identifier resolves to at most one account.
"""

import re
import secrets
from typing import Any

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
PHONE_SEPARATORS = re.compile(r"[\s\-().]")
USERNAME_SAFE = re.compile(r"[^A-Za-z0-9_.-]")


class IdentityError(ValueError):
    """Raised when an identifier is malformed or points at no single account."""


def normalize_email(raw: str) -> str:
    """Return a lowercased, validated address."""
    value = raw.strip().lower()
    try:
        validate_email(value)
    except ValidationError as error:
        raise IdentityError("Enter a valid email address.") from error
    return value


def normalize_phone(raw: str) -> str:
    """Return an E.164 number, tolerating the spacing people actually type."""
    value = PHONE_SEPARATORS.sub("", raw.strip())
    if not PHONE_PATTERN.fullmatch(value):
        raise IdentityError("Enter a phone number in international format, such as +14155550101.")
    return value


def user_by_id(subject: str) -> Any | None:
    """Return the account this primary key names, or ``None`` if it names none.

    The subject arrives from the challenge store as a string, and the primary key
    it has to become depends on the user model: a UUID here, an integer under
    Django's stock model, whatever a project chose. A value that cannot be one is
    "no such account", not a crash -- and an unguarded ``filter(pk=...)`` gives a
    crash, because Django validates the key before it queries.
    """
    if not subject:
        return None
    try:
        return get_user_model()._default_manager.filter(pk=subject).first()
    except (ValidationError, ValueError, TypeError):
        return None


def user_by_email(email: str) -> Any | None:
    """Return the single account using this address, or ``None``.

    The shipped user model makes ``email`` unique, so this normally finds at most
    one. The guard is for a project that has swapped ``AUTH_USER_MODEL`` for a
    model that does not -- Django's stock one among them. There, an address
    several accounts share is ambiguous rather than a login, and refusing is the
    only safe reading: picking one would hand an attacker whichever sorts first.
    """
    user_model = get_user_model()
    if not hasattr(user_model, "email"):
        return None
    matches = list(user_model._default_manager.filter(email__iexact=email)[:2])
    if len(matches) > 1:
        raise IdentityError("More than one account uses this email address.")
    return matches[0] if matches else None


def user_by_phone(phone: str, *, verified_only: bool = True) -> Any | None:
    """Return the account that owns this number, or ``None``."""
    from infrastructure.auth.core.models import PhoneNumber

    queryset = PhoneNumber.objects.select_related("user").filter(number=phone)
    if verified_only:
        queryset = queryset.filter(is_verified=True)
    record = queryset.first()
    return record.user if record else None


def user_by_login(identifier: str) -> Any | None:
    """Resolve a username-or-email login box to an account."""
    user_model = get_user_model()
    username_field = user_model.USERNAME_FIELD
    match = user_model._default_manager.filter(**{f"{username_field}__iexact": identifier}).first()
    if match is not None:
        return match
    if username_field != "email" and "@" in identifier:
        return user_by_email(normalize_email(identifier))
    return None


def _unique_username(base: str) -> str:
    """Derive a free username from a seed, widening it only as far as needed."""
    user_model = get_user_model()
    username_field = user_model.USERNAME_FIELD
    max_length = getattr(user_model._meta.get_field(username_field), "max_length", 150) or 150
    seed = USERNAME_SAFE.sub("", base) or "user"
    for _ in range(10):
        suffix = secrets.token_hex(4)
        candidate = f"{seed[: max_length - len(suffix) - 1]}.{suffix}"
        if not user_model._default_manager.filter(**{username_field: candidate}).exists():
            return candidate
    raise IdentityError("Could not allocate a username.")


def _create_user(attributes: dict[str, str]) -> Any:
    user_model = get_user_model()
    user = user_model._default_manager.create_user(**attributes)
    if not user.has_usable_password():
        return user
    user.set_unusable_password()
    user.save(update_fields=["password"])
    return user


def create_user_for_email(email: str) -> Any:
    """Create a passwordless account owning this verified address."""
    user_model = get_user_model()
    username_field = user_model.USERNAME_FIELD
    attributes = {"email": email} if hasattr(user_model, "email") else {}
    if username_field != "email":
        attributes[username_field] = _unique_username(email.split("@", 1)[0])
    else:
        attributes[username_field] = email
    return _create_user(attributes)


def create_user_for_phone(phone: str) -> Any:
    """Create a passwordless account owning this verified number."""
    from infrastructure.auth.core.models import PhoneNumber

    user_model = get_user_model()
    username_field = user_model.USERNAME_FIELD
    attributes = {username_field: _unique_username(f"user{phone.lstrip('+')}")}
    with transaction.atomic():
        user = _create_user(attributes)
        PhoneNumber.objects.create(user=user, number=phone, is_verified=True, is_primary=True)
    return user


def account_email(user: Any) -> str:
    """Return the address a message to this account should go to."""
    return str(getattr(user, "email", "") or "")


def account_phone(user: Any) -> str:
    """Return the account's primary verified number, or an empty string."""
    from infrastructure.auth.core.models import PhoneNumber

    record = (
        PhoneNumber.objects.filter(user=user, is_verified=True)
        .order_by("-is_primary", "created_at")
        .first()
    )
    return record.number if record else ""
