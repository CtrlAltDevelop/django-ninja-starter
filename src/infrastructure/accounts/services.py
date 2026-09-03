"""What an account can read and change about itself.

The whole decision lives here: which fields a profile update may touch, what a
value has to look like before it reaches the column, and how an account
describes itself back to a caller. The three transport packages beside this file
render that answer; none of them re-decides any part of it.

Deliberately not here: changing the email address or the password. Both are
authentication, both need a challenge to be safe, and both already live with the
method that owns them.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from infrastructure.accounts.models import Profile
from infrastructure.common.errors import ApiError
from infrastructure.common.responses import ResponseTitle


@dataclass(frozen=True, slots=True)
class ProfileView:
    """A profile as every transport renders it. Dates and blanks already resolved."""

    display_name: str
    avatar_url: str
    bio: str
    locale: str
    timezone: str
    date_of_birth: str
    marketing_opt_in: bool


@dataclass(frozen=True, slots=True)
class AccountView:
    """The signed-in account, as it should describe itself back to a caller."""

    id: str
    username: str
    email: str
    email_verified: bool
    is_active: bool
    is_staff: bool
    date_joined: str
    last_login: str
    profile: ProfileView


class AccountService:
    """Read and update the account behind a credential."""

    # Stored as a date, carried as an ISO string, cleared with an empty one.
    DATE_FIELDS = frozenset({"date_of_birth"})
    URL_FIELDS = frozenset({"avatar_url"})

    def __init__(self) -> None:
        self._validate_url = URLValidator()

    def view(self, user: Any) -> AccountView:
        """Describe one account, profile included."""
        return AccountView(
            id=str(user.pk),
            username=user.username,
            email=user.email or "",
            email_verified=user.is_email_verified,
            is_active=user.is_active,
            is_staff=user.is_staff,
            date_joined=user.date_joined.isoformat(),
            last_login=user.last_login.isoformat() if user.last_login else "",
            profile=self.profile_view(user.profile),
        )

    def profile_view(self, profile: Profile) -> ProfileView:
        return ProfileView(
            display_name=profile.display_name,
            avatar_url=profile.avatar_url,
            bio=profile.bio,
            locale=profile.locale,
            timezone=profile.timezone,
            date_of_birth=profile.date_of_birth.isoformat() if profile.date_of_birth else "",
            marketing_opt_in=profile.marketing_opt_in,
        )

    def update_profile(self, user: Any, changes: dict[str, Any]) -> AccountView:
        """Apply a partial update and return the account as it now stands.

        An omitted field is left alone; an empty string clears one. Callers are
        expected to have dropped anything that was not supplied -- what arrives
        here is the set of fields the caller meant to change.
        """
        if not changes:
            raise ApiError(
                "Supply at least one field to update.",
                status=400,
                title=ResponseTitle.VALIDATION_ERROR,
            )
        unknown = set(changes) - {field.name for field in ProfileView.__dataclass_fields__.values()}
        if unknown:
            raise ApiError(
                f"Unknown profile field: {', '.join(sorted(unknown))}.",
                status=400,
                title=ResponseTitle.VALIDATION_ERROR,
            )

        profile = user.profile
        for field, value in changes.items():
            setattr(profile, field, self._clean(field, value))
        profile.save(update_fields=[*changes, "updated_at"])
        return self.view(user)

    def _clean(self, field: str, value: Any) -> Any:
        if field in self.DATE_FIELDS:
            return self._as_date(field, value)
        if isinstance(value, str):
            return self._checked(field, value)
        return value

    def _as_date(self, field: str, value: Any) -> date | None:
        if not value:
            return None
        try:
            return date.fromisoformat(str(value))
        except ValueError as error:
            raise ApiError(
                f"{field} must be an ISO date, such as 1990-04-23.",
                status=400,
                title=ResponseTitle.VALIDATION_ERROR,
            ) from error

    def _checked(self, field: str, value: str) -> str:
        """Refuse a value the column cannot hold, or a URL that is not one.

        The widths come from the model rather than being restated here, so they
        cannot drift apart from it. Without this the oversized value reaches the
        database, where SQLite shrugs and PostgreSQL raises -- turning a client's
        bad input into a 500 in exactly the deployments that matter.
        """
        max_length = Profile._meta.get_field(field).max_length
        if max_length and len(value) > max_length:
            raise ApiError(
                f"{field} must be at most {max_length} characters.",
                status=400,
                title=ResponseTitle.VALIDATION_ERROR,
            )
        if field in self.URL_FIELDS and value:
            try:
                self._validate_url(value)
            except ValidationError as error:
                raise ApiError(
                    f"{field} must be a valid URL.",
                    status=400,
                    title=ResponseTitle.VALIDATION_ERROR,
                ) from error
        return value


account_service = AccountService()
