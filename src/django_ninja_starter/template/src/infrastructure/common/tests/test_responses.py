"""The envelope is the contract, so it is tested as one.

These go through the real API rather than calling :func:`envelope` directly:
what matters is that a body a client receives has the six keys, whichever layer
produced it -- a view, a raised :class:`ApiError`, or the framework refusing a
request before any view ran.
"""

from pathlib import Path

from django.conf import settings
from django.test import Client

from infrastructure.common.responses import (
    DESCRIPTIONS,
    ENVELOPE_KEYS,
    ResponseTitle,
    envelope,
    title_for_status,
)

PASSWORD = "corr3ct-horse-battery"
SIGNUP = "/api/v1/auth/password/signup"
LOGIN = "/api/v1/auth/password/login"


def test_every_title_has_a_description() -> None:
    """A title with no gloss is a key nobody can read in the docs or a log."""
    assert set(DESCRIPTIONS) == set(ResponseTitle)


def test_a_status_with_no_title_of_its_own_still_gets_one() -> None:
    assert title_for_status(200) is ResponseTitle.SUCCESS
    assert title_for_status(418) is ResponseTitle.BAD_REQUEST
    assert title_for_status(502) is ResponseTitle.INTERNAL_ERROR


def test_an_empty_error_list_is_null_rather_than_empty() -> None:
    """`errors` is the flag clients read; `[]` and `null` must not both mean no."""
    assert envelope(status=200)["errors"] is None
    assert envelope(status=400, errors=["nope"])["errors"] == ["nope"]


def test_a_successful_body_is_wrapped_with_the_payload_intact() -> None:
    response = Client().get("/api/v1/health/live")
    body = response.json()

    assert list(body) == list(ENVELOPE_KEYS)
    assert body["data"] == {"status": "ok", "checks": {}}
    assert body["errors"] is None
    assert body["isSuccess"] is True
    assert body["statusCode"] == 200
    assert body["title"] == ResponseTitle.SUCCESS
    assert body["description"]


def test_a_failing_check_is_still_a_success_envelope() -> None:
    """`isSuccess` follows the status, and 503 here is the answer, not a failure.

    Readiness reports on the database rather than refusing to answer, so the
    envelope has to say the request failed -- a client that only looked at
    `data` would read `"unavailable"` as though it were fine.
    """
    body = envelope(status=503, data={"status": "unavailable"})

    assert body["isSuccess"] is False
    assert body["title"] == ResponseTitle.SERVICE_UNAVAILABLE


def test_a_refused_request_names_the_reason_a_client_can_translate(db: None) -> None:
    client = Client()
    client.post(
        SIGNUP,
        {"identifier": "zoe", "password": PASSWORD, "email": "zoe@example.com"},
        content_type="application/json",
    )

    response = client.post(
        LOGIN,
        {"identifier": "zoe", "password": "not-the-password"},
        content_type="application/json",
    )
    body = response.json()

    assert response.status_code == 401
    assert body["isSuccess"] is False
    assert body["data"] is None
    assert body["title"] == ResponseTitle.INVALID_CREDENTIALS
    assert body["errors"] == ["Those credentials are not valid."]
    assert body["description"] == "Those credentials are not valid."


def test_a_body_that_does_not_validate_is_reported_field_by_field(db: None) -> None:
    """Django Ninja's own 422 is enveloped too, with its errors flattened."""
    response = Client().post(SIGNUP, {"identifier": "zoe"}, content_type="application/json")
    body = response.json()

    assert response.status_code == 422
    assert body["title"] == ResponseTitle.VALIDATION_ERROR
    assert any("password" in message for message in body["errors"])
    assert body["data"] is None


def test_a_missing_credential_is_enveloped_by_the_framework(db: None) -> None:
    response = Client().get("/api/v1/users/me")
    body = response.json()

    assert response.status_code == 401
    assert body["title"] == ResponseTitle.AUTHENTICATION_REQUIRED
    assert body["isSuccess"] is False


def test_the_published_schema_documents_the_envelope_it_sends() -> None:
    """A client generated from `/openapi.json` must see the wrapper, not the payload."""
    schema = Client().get("/api/v1/openapi.json").json()
    live = schema["paths"]["/api/v1/health/live"]["get"]
    wrapper = live["responses"]["200"]["content"]["application/json"]["schema"]

    assert list(wrapper["properties"]) == list(ENVELOPE_KEYS)
    assert wrapper["properties"]["title"] == {"$ref": "#/components/schemas/ResponseTitle"}
    assert wrapper["properties"]["data"]["$ref"].endswith("HealthResponse")
    assert "default" in live["responses"]
    assert schema["components"]["schemas"]["ResponseTitle"]["enum"][0] == ResponseTitle.SUCCESS


def test_the_documented_titles_are_the_titles_that_exist() -> None:
    """A title absent from the table is one no client will ever translate."""
    page = (Path(settings.BASE_DIR) / "docs" / "responses.md").read_text(encoding="utf-8")

    assert [title.value for title in ResponseTitle if f"`{title.value}`" not in page] == []
