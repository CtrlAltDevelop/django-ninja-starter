"""Request bodies for the SMS-code endpoints."""

from ninja import Schema


class StartIn(Schema):
    phone: str


class VerifyIn(Schema):
    ticket: str
    code: str


class LogoutIn(Schema):
    token: str = ""
