"""One shape for every JSON body this API returns.

A client that has to guess where the payload is -- top level here, under a key
there, an error that is sometimes ``detail`` and sometimes a list of pydantic
dictionaries -- writes a branch for each endpoint. So every response leaves this
API in the same envelope:

.. code-block:: json

    {
      "errors": null,
      "data": {"token_type": "Bearer"},
      "isSuccess": true,
      "statusCode": 200,
      "title": "SUCCESS",
      "description": "The request succeeded."
    }

``title`` is the part worth understanding. It is a stable enum member, never a
sentence, so a client can key its own translations off it and show the user a
message in their language. ``description`` is the English gloss of the same
thing, for the developer reading a log or a failed request in a console -- it is
not meant to be shown to an end user and is not translated.

The wrapping happens in :class:`EnvelopeRenderer`, at the point where the body is
serialised, rather than in each endpoint. Views therefore go on returning their
own schemas, and the envelope cannot be forgotten on the one path nobody tested.
Handlers that need to say more than the status code does -- an error's message,
a domain-specific title -- build the body themselves with :func:`envelope`, and
the renderer leaves an :class:`Enveloped` body alone.

The one body this does not cover is a 500 raised by a bug: Django Ninja re-raises
those for Django to handle, so they never reach this renderer.
"""

import json
from enum import StrEnum
from typing import Any

from django.http import HttpRequest, JsonResponse
from ninja import NinjaAPI, Schema
from ninja.renderers import JSONRenderer

ENVELOPE_KEYS = ("errors", "data", "isSuccess", "statusCode", "title", "description")


class ResponseTitle(StrEnum):
    """The i18n key a client translates. Add members; never reword them.

    The value is the member name, so what a client matches on is what the code
    reads. Renaming one is a breaking change to every client that translated it,
    which is the whole reason it is an enum and not the human sentence next to it.
    """

    # Success
    SUCCESS = "SUCCESS"
    CREATED = "CREATED"

    # Failures that the status code alone already describes
    BAD_REQUEST = "BAD_REQUEST"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    GONE = "GONE"
    RATE_LIMITED = "RATE_LIMITED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # Accounts and passwords
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    INVALID_IDENTIFIER = "INVALID_IDENTIFIER"
    USERNAME_REQUIRED = "USERNAME_REQUIRED"
    ACCOUNT_EXISTS = "ACCOUNT_EXISTS"
    ACCOUNT_NOT_FOUND = "ACCOUNT_NOT_FOUND"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    PHONE_IN_USE = "PHONE_IN_USE"
    ACCOUNT_HAS_NO_EMAIL = "ACCOUNT_HAS_NO_EMAIL"
    WEAK_PASSWORD = "WEAK_PASSWORD"
    INCORRECT_PASSWORD = "INCORRECT_PASSWORD"

    # Codes and the sign-ins they belong to
    INVALID_CODE = "INVALID_CODE"
    CODE_NOT_REQUESTED = "CODE_NOT_REQUESTED"
    CODE_EXPIRED = "CODE_EXPIRED"
    TOO_MANY_ATTEMPTS = "TOO_MANY_ATTEMPTS"
    CHALLENGE_INVALID = "CHALLENGE_INVALID"
    SIGN_IN_EXPIRED = "SIGN_IN_EXPIRED"

    # Second factors
    SECOND_FACTOR_UNSPECIFIED = "SECOND_FACTOR_UNSPECIFIED"
    SECOND_FACTOR_NOT_ENABLED = "SECOND_FACTOR_NOT_ENABLED"
    SECOND_FACTOR_NOT_SET_UP = "SECOND_FACTOR_NOT_SET_UP"
    SECOND_FACTOR_EXISTS = "SECOND_FACTOR_EXISTS"
    SECOND_FACTOR_NOT_CODE_BASED = "SECOND_FACTOR_NOT_CODE_BASED"
    SECOND_FACTOR_NO_DESTINATION = "SECOND_FACTOR_NO_DESTINATION"
    ENROLMENT_NOT_STARTED = "ENROLMENT_NOT_STARTED"

    # Credentials and sessions
    TOKEN_REQUIRED = "TOKEN_REQUIRED"
    TOKEN_INVALID = "TOKEN_INVALID"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_REUSED = "TOKEN_REUSED"
    TOKENS_UNSUPPORTED = "TOKENS_UNSUPPORTED"
    SESSION_ENDED = "SESSION_ENDED"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    NOT_SIGNED_IN = "NOT_SIGNED_IN"

    # Social sign-in
    OAUTH_NOT_CONFIGURED = "OAUTH_NOT_CONFIGURED"
    OAUTH_STATE_MISSING = "OAUTH_STATE_MISSING"
    OAUTH_FAILED = "OAUTH_FAILED"


DESCRIPTIONS: dict[ResponseTitle, str] = {
    ResponseTitle.SUCCESS: "The request succeeded.",
    ResponseTitle.CREATED: "The resource was created.",
    ResponseTitle.BAD_REQUEST: "The request was refused.",
    ResponseTitle.VALIDATION_ERROR: "The request body did not validate.",
    ResponseTitle.AUTHENTICATION_REQUIRED: "This endpoint needs a credential.",
    ResponseTitle.FORBIDDEN: "This account may not do that.",
    ResponseTitle.NOT_FOUND: "There is no such thing to act on.",
    ResponseTitle.CONFLICT: "That would conflict with something that already exists.",
    ResponseTitle.GONE: "What the request refers to has expired.",
    ResponseTitle.RATE_LIMITED: "Too many attempts. Wait and try again.",
    ResponseTitle.SERVICE_UNAVAILABLE: "A dependency this endpoint needs is unavailable.",
    ResponseTitle.INTERNAL_ERROR: "The request failed for a reason the client cannot fix.",
    ResponseTitle.INVALID_CREDENTIALS: "The identifier or the password is wrong.",
    ResponseTitle.INVALID_IDENTIFIER: "That is not a usable email address or phone number.",
    ResponseTitle.USERNAME_REQUIRED: "A username is required.",
    ResponseTitle.ACCOUNT_EXISTS: "An account already uses that identifier.",
    ResponseTitle.ACCOUNT_NOT_FOUND: "No account uses that identifier.",
    ResponseTitle.ACCOUNT_DISABLED: "The account exists but may not sign in.",
    ResponseTitle.PHONE_IN_USE: "Another account already uses that phone number.",
    ResponseTitle.ACCOUNT_HAS_NO_EMAIL: "The account has no email address to send to.",
    ResponseTitle.WEAK_PASSWORD: "The password does not meet the policy.",
    ResponseTitle.INCORRECT_PASSWORD: "The current password given is not this account's.",
    ResponseTitle.INVALID_CODE: "The code is wrong.",
    ResponseTitle.CODE_NOT_REQUESTED: "Ask for a code before trying to verify one.",
    ResponseTitle.CODE_EXPIRED: "The code has expired. Ask for another.",
    ResponseTitle.TOO_MANY_ATTEMPTS: "Too many wrong attempts. Start again.",
    ResponseTitle.CHALLENGE_INVALID: "The ticket is unknown or no longer usable.",
    ResponseTitle.SIGN_IN_EXPIRED: "This sign-in is no longer valid. Start again.",
    ResponseTitle.SECOND_FACTOR_UNSPECIFIED: "The request must say which second factor it means.",
    ResponseTitle.SECOND_FACTOR_NOT_ENABLED: "This deployment does not offer that second factor.",
    ResponseTitle.SECOND_FACTOR_NOT_SET_UP: "This account has not set that second factor up.",
    ResponseTitle.SECOND_FACTOR_EXISTS: "That second factor is already set up.",
    ResponseTitle.SECOND_FACTOR_NOT_CODE_BASED: "That second factor does not use a sent code.",
    ResponseTitle.SECOND_FACTOR_NO_DESTINATION: "That second factor has no destination on file.",
    ResponseTitle.ENROLMENT_NOT_STARTED: "Begin enrolment before confirming it.",
    ResponseTitle.TOKEN_REQUIRED: "A token is required and none was given.",
    ResponseTitle.TOKEN_INVALID: "The token is not one this deployment issued.",
    ResponseTitle.TOKEN_EXPIRED: "The token has expired.",
    ResponseTitle.TOKEN_REUSED: "A spent token was presented again; the session was ended.",
    ResponseTitle.TOKENS_UNSUPPORTED: "This deployment does not issue that kind of credential.",
    ResponseTitle.SESSION_ENDED: "The session has ended. Sign in again.",
    ResponseTitle.SESSION_NOT_FOUND: "No such session.",
    ResponseTitle.NOT_SIGNED_IN: "There is no signed-in session to act on.",
    ResponseTitle.OAUTH_NOT_CONFIGURED: "This provider has no credentials configured.",
    ResponseTitle.OAUTH_STATE_MISSING: "The callback arrived without its state.",
    ResponseTitle.OAUTH_FAILED: "The provider did not complete the sign-in.",
}

TITLE_FOR_STATUS: dict[int, ResponseTitle] = {
    201: ResponseTitle.CREATED,
    400: ResponseTitle.BAD_REQUEST,
    401: ResponseTitle.AUTHENTICATION_REQUIRED,
    403: ResponseTitle.FORBIDDEN,
    404: ResponseTitle.NOT_FOUND,
    409: ResponseTitle.CONFLICT,
    410: ResponseTitle.GONE,
    422: ResponseTitle.VALIDATION_ERROR,
    429: ResponseTitle.RATE_LIMITED,
    503: ResponseTitle.SERVICE_UNAVAILABLE,
}


def title_for_status(status: int) -> ResponseTitle:
    """The title to use when nothing more specific was supplied."""
    if status in TITLE_FOR_STATUS:
        return TITLE_FOR_STATUS[status]
    if status < 400:
        return ResponseTitle.SUCCESS
    if status < 500:
        return ResponseTitle.BAD_REQUEST
    return ResponseTitle.INTERNAL_ERROR


class Enveloped(dict[str, Any]):
    """A body that is already in the envelope. The renderer passes it through."""


def envelope(
    *,
    status: int,
    data: Any = None,
    errors: list[str] | None = None,
    title: ResponseTitle | None = None,
    description: str = "",
) -> Enveloped:
    """Build the one response shape, filling in whatever was not spelled out."""
    resolved = title or title_for_status(status)
    return Enveloped(
        errors=errors or None,
        data=data,
        isSuccess=status < 400,
        statusCode=status,
        title=resolved.value,
        description=description or DESCRIPTIONS[resolved],
    )


def envelope_response(
    *,
    status: int,
    data: Any = None,
    errors: list[str] | None = None,
    title: ResponseTitle | None = None,
    description: str = "",
) -> JsonResponse:
    """The same envelope, for the few views that answer outside the API layer.

    The OAuth callbacks are plain Django views -- they may end in a redirect --
    so they never pass through the renderer and have to wrap their own bodies.
    """
    return JsonResponse(
        envelope(
            status=status,
            data=data,
            errors=errors,
            title=title,
            description=description,
        ),
        status=status,
    )


class EnvelopeRenderer(JSONRenderer):
    """Wrap every JSON body on its way out, unless it is wrapped already."""

    def render(self, request: HttpRequest, data: Any, *, response_status: int) -> Any:
        if not isinstance(data, Enveloped):
            data = envelope(status=response_status, data=data)
        return json.dumps(data, cls=self.encoder_class, **self.json_dumps_params)


class EnvelopeOut(Schema):
    """The envelope itself, so that ``/docs`` has something to point at.

    Endpoints keep declaring the schema of what they put in ``data``; this class
    documents the six keys wrapped around it, and
    :func:`document_envelope` is what rewrites the published operations to say so.
    """

    errors: list[str] | None = None
    data: Any = None
    isSuccess: bool
    statusCode: int
    title: ResponseTitle
    description: str


def _envelope_schema(data_schema: dict[str, Any] | None) -> dict[str, Any]:
    """The OpenAPI schema for one enveloped body, around a given ``data``."""
    return {
        "title": "ResponseEnvelope",
        "type": "object",
        "properties": {
            "errors": {"type": ["array", "null"], "items": {"type": "string"}},
            "data": data_schema if data_schema is not None else {"type": "null"},
            "isSuccess": {"type": "boolean"},
            "statusCode": {"type": "integer"},
            "title": {"$ref": "#/components/schemas/ResponseTitle"},
            "description": {"type": "string"},
        },
        "required": list(ENVELOPE_KEYS),
    }


def document_envelope(schema: dict[str, Any], media_type: str) -> dict[str, Any]:
    """Rewrite a generated OpenAPI document to describe what is actually sent.

    Django Ninja documents the schema a view returns, which is now the *inside*
    of the envelope. Rather than restate the wrapper on 50-odd operations by
    hand -- where it would be wrong the first time somebody added one -- each
    documented response is wrapped here, once, on the way out.

    Every operation also gains a ``default`` response, because the errors that
    are raised rather than returned -- a rejected body, a missing credential --
    are answers no endpoint declares but every endpoint can give.
    """
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components["ResponseTitle"] = {
        "title": "ResponseTitle",
        "description": "Stable key for the message a client shows its user.",
        "type": "string",
        "enum": [title.value for title in ResponseTitle],
    }
    for operations in schema.get("paths", {}).values():
        for operation in operations.values():
            responses = operation.get("responses", {})
            for response in responses.values():
                content = response.get("content", {}).get(media_type)
                if content is not None:
                    content["schema"] = _envelope_schema(content.get("schema"))
            responses["default"] = {
                "description": "An error, in the same envelope, with `data` null.",
                "content": {media_type: {"schema": _envelope_schema(None)}},
            }
    return schema


class EnvelopeAPI(NinjaAPI):
    """A :class:`~ninja.NinjaAPI` whose documentation matches its renderer."""

    def get_openapi_schema(
        self,
        *,
        path_prefix: str | None = None,
        path_params: dict[str, Any] | None = None,
    ) -> Any:
        schema = super().get_openapi_schema(path_prefix=path_prefix, path_params=path_params)
        return document_envelope(schema, self.renderer.media_type or "application/json")
