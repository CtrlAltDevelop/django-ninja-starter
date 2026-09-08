"""What the signed-in account can read and change about itself, over HTTP.

Mounted at ``/users`` whatever else is enabled, because the account exists
whether or not any particular login method does. Everything here needs a
credential, and any method's credential will do: see
:mod:`infrastructure.auth.core.sessions` for why they are interchangeable.

The decisions are :class:`AccountService`'s. What is left here is the part that
is genuinely REST: the PATCH semantics -- an omitted field is not a cleared one
-- which is why the payload is read with ``exclude_unset``.
"""

from django.http import HttpRequest
from ninja import Router

from infrastructure.accounts.rest.schemas import AccountOut, MessageOut, ProfileIn, ProfileOut
from infrastructure.accounts.services import AccountView, account_service
from infrastructure.auth.core.sessions import api_auth

router = Router()


def account_out(account: AccountView) -> AccountOut:
    """Render the service's answer as the documented response body."""
    return AccountOut(
        id=account.id,
        username=account.username,
        email=account.email,
        email_verified=account.email_verified,
        is_active=account.is_active,
        is_staff=account.is_staff,
        date_joined=account.date_joined,
        last_login=account.last_login,
        profile=ProfileOut(
            display_name=account.profile.display_name,
            avatar_url=account.profile.avatar_url,
            bio=account.profile.bio,
            locale=account.profile.locale,
            timezone=account.profile.timezone,
            date_of_birth=account.profile.date_of_birth,
            marketing_opt_in=account.profile.marketing_opt_in,
        ),
    )


@router.get(
    "/me",
    response=AccountOut,
    auth=api_auth,
    summary="The signed-in account and its profile",
)
def me(request: HttpRequest) -> AccountOut:
    return account_out(account_service.view(request.user))


@router.patch(
    "/me/profile",
    response={200: AccountOut, 400: MessageOut},
    auth=api_auth,
    summary="Update the signed-in account's profile",
)
def update_profile(request: HttpRequest, payload: ProfileIn) -> AccountOut:
    """Apply a partial update.

    ``exclude_unset`` is what makes this a PATCH rather than a PUT, and
    ``exclude_none`` is what makes clearing a field mean sending an empty string
    -- the distinction the schema's ``None`` defaults exist to draw.
    """
    changes = payload.dict(exclude_unset=True, exclude_none=True)
    return account_out(account_service.update_profile(request.user, changes))
