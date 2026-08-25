"""Request bodies for the email-code endpoints."""

from ninja import Schema


class StartIn(Schema):
    email: str


class VerifyIn(Schema):
    ticket: str
    code: str


class LogoutIn(Schema):
    token: str = ""
