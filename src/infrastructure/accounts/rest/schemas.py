"""What the account endpoints accept and return."""

from ninja import Schema


class ProfileOut(Schema):
    display_name: str
    avatar_url: str
    bio: str
    locale: str
    timezone: str
    date_of_birth: str = ""
    marketing_opt_in: bool


class AccountOut(Schema):
    """The signed-in account, as it should describe itself back to a client."""

    id: str
    username: str
    email: str = ""
    email_verified: bool
    is_active: bool
    is_staff: bool
    date_joined: str
    last_login: str = ""
    profile: ProfileOut


class ProfileIn(Schema):
    """A partial update. Anything omitted is left alone.

    Every field is optional and ``None`` means "not supplied" -- which is why
    clearing a field is done by sending an empty string rather than by omitting
    it, a distinction a PATCH has to make somehow.
    """

    display_name: str | None = None
    avatar_url: str | None = None
    bio: str | None = None
    locale: str | None = None
    timezone: str | None = None
    date_of_birth: str | None = None
    marketing_opt_in: bool | None = None


class MessageOut(Schema):
    detail: str
