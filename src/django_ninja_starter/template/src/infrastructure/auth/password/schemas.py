"""Request bodies for the password endpoints."""

from ninja import Schema


class SignupIn(Schema):
    identifier: str
    password: str
    email: str = ""


class LoginIn(Schema):
    identifier: str
    password: str


class LogoutIn(Schema):
    token: str = ""


class ForgotIn(Schema):
    email: str


class ForgotOut(Schema):
    """Always the same shape, whether or not the address has an account.

    The ticket is useless without the code that was emailed, so returning one
    unconditionally costs nothing and keeps the response from confirming which
    addresses are registered.
    """

    detail: str
    ticket: str
    expires_in: int


class ResetIn(Schema):
    ticket: str
    code: str
    password: str


class ChangeIn(Schema):
    current_password: str
    new_password: str
