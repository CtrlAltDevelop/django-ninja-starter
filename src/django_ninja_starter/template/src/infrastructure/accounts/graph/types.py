"""GraphQL types for the account and its profile.

A one-for-one rendering of what :class:`AccountService` returns. Dates are
strings here for the same reason they are strings in the REST body: the service
has already resolved "no date" to an empty string, and a nullable scalar would
make every client handle a second empty case.
"""

import strawberry

from infrastructure.accounts.services import AccountView, ProfileView


@strawberry.type
class ProfileType:
    display_name: str
    avatar_url: str
    bio: str
    locale: str
    timezone: str
    date_of_birth: str
    marketing_opt_in: bool

    @classmethod
    def from_view(cls, profile: ProfileView) -> "ProfileType":
        return cls(
            display_name=profile.display_name,
            avatar_url=profile.avatar_url,
            bio=profile.bio,
            locale=profile.locale,
            timezone=profile.timezone,
            date_of_birth=profile.date_of_birth,
            marketing_opt_in=profile.marketing_opt_in,
        )


@strawberry.type
class AccountType:
    id: str
    username: str
    email: str
    email_verified: bool
    is_active: bool
    is_staff: bool
    date_joined: str
    last_login: str
    profile: ProfileType

    @classmethod
    def from_view(cls, account: AccountView) -> "AccountType":
        return cls(
            id=account.id,
            username=account.username,
            email=account.email,
            email_verified=account.email_verified,
            is_active=account.is_active,
            is_staff=account.is_staff,
            date_joined=account.date_joined,
            last_login=account.last_login,
            profile=ProfileType.from_view(account.profile),
        )


@strawberry.input
class ProfileInput:
    """A partial update. Anything left unset is left alone.

    GraphQL has ``UNSET`` for exactly the distinction a PATCH body needs, so
    "not supplied" and "cleared" stay two different things here without the
    ``None``-means-omitted convention the REST schema has to use.
    """

    display_name: str | None = strawberry.UNSET
    avatar_url: str | None = strawberry.UNSET
    bio: str | None = strawberry.UNSET
    locale: str | None = strawberry.UNSET
    timezone: str | None = strawberry.UNSET
    date_of_birth: str | None = strawberry.UNSET
    marketing_opt_in: bool | None = strawberry.UNSET

    def changes(self) -> dict[str, object]:
        """Return only the fields this input actually carried."""
        return {
            name: value
            for name, value in vars(self).items()
            if value is not strawberry.UNSET and value is not None
        }
