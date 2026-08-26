"""What the signed-in account can read and change about itself.

Mounted at ``/users`` whatever else is enabled, because the account exists
whether or not any particular login method does. Everything here needs a
credential, and any method's credential will do: see
:mod:`infrastructure.auth.core.sessions` for why they are interchangeable.

Deliberately not here: changing the email address or the password. Both are
authentication, both need a challenge to be safe, and both already live with the
method that owns them.
"""

from datetime import date
from typing import Any

from django.http import HttpRequest
from ninja import Router

from infrastructure.accounts.models import Profile
from infrastructure.accounts.schemas import AccountOut, MessageOut, ProfileIn, ProfileOut
from infrastructure.auth.core.sessions import api_auth
from infrastructure.common.errors import ApiError

router = Router()

DATE_FIELDS = {"date_of_birth"}


def _profile_out(profile: Profile) -> ProfileOut:
    return ProfileOut(
        display_name=profile.display_name,
        avatar_url=profile.avatar_url,
        bio=profile.bio,
        locale=profile.locale,
        timezone=profile.timezone,
        date_of_birth=profile.date_of_birth.isoformat() if profile.date_of_birth else "",
        marketing_opt_in=profile.marketing_opt_in,
    )


def _account_out(user: Any) -> AccountOut:
    return AccountOut(
        id=str(user.pk),
        username=user.username,
        email=user.email or "",
        email_verified=user.is_email_verified,
        is_active=user.is_active,
        is_staff=user.is_staff,
        date_joined=user.date_joined.isoformat(),
        last_login=user.last_login.isoformat() if user.last_login else "",
        profile=_profile_out(user.profile),
    )


@router.get(
    "/me",
    response=AccountOut,
    auth=api_auth,
    summary="The signed-in account and its profile",
)
def me(request: HttpRequest) -> AccountOut:
    return _account_out(request.user)


@router.patch(
    "/me/profile",
    response={200: AccountOut, 400: MessageOut},
    auth=api_auth,
    summary="Update the signed-in account's profile",
)
def update_profile(request: HttpRequest, payload: ProfileIn) -> AccountOut:
    """Apply a partial update.

    An omitted field is left alone; an empty string clears one. That distinction
    is the whole reason the schema uses ``None`` defaults rather than empty ones.
    """
    profile = request.user.profile
    supplied = payload.dict(exclude_unset=True, exclude_none=True)
    if not supplied:
        raise ApiError("Supply at least one field to update.", status=400)

    for field, value in supplied.items():
        if field in DATE_FIELDS and value:
            try:
                value = date.fromisoformat(str(value))
            except ValueError as error:
                raise ApiError(f"{field} must be an ISO date, such as 1990-04-23.", 400) from error
        elif field in DATE_FIELDS:
            value = None
        setattr(profile, field, value)
    profile.save(update_fields=[*supplied, "updated_at"])

    return _account_out(request.user)
