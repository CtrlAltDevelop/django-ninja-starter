"""Request and response bodies for the magic-link endpoints."""

from ninja import Schema


class StartIn(Schema):
    email: str


class StartOut(Schema):
    detail: str
    destination: str
    expires_in: int


class VerifyIn(Schema):
    token: str


class LogoutIn(Schema):
    token: str = ""
