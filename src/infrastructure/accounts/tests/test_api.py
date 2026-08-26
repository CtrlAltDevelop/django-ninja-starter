"""The account endpoints, and the promise that every method's token opens them.

The second half of this file is the one that matters most. A credential from a
password login, an emailed code, a magic link and an SMS code must all be
accepted by an endpoint that knows nothing about any of them -- that is the whole
point of every method funnelling through one issuer, and it is worth a test that
would fail loudly if a method ever grew its own token format.
"""

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from infrastructure.auth.core import delivery
from infrastructure.auth.core.challenges import get_challenge_store

ME = "/api/v1/users/me"
PROFILE = "/api/v1/users/me/profile"
PASSWORD = "corr3ct-horse-battery"
User = get_user_model()


def _bearer(token: str) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _password_token(client: Client, identifier: str = "zoe") -> str:
    response = client.post(
        "/api/v1/auth/password/signup",
        {"identifier": identifier, "password": PASSWORD, "email": f"{identifier}@example.com"},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    token: str = response.json()["credentials"]["access_token"]
    return token


@pytest.fixture
def token(db: None) -> str:
    return _password_token(Client())


def test_me_describes_the_signed_in_account(token: str) -> None:
    body = Client().get(ME, **_bearer(token)).json()

    assert body["username"] == "zoe"
    assert body["email"] == "zoe@example.com"
    assert body["is_active"] is True
    assert body["is_staff"] is False
    assert body["profile"]["locale"] == "en-us"


def test_me_needs_a_credential(db: None) -> None:
    assert Client().get(ME).status_code == 401


def test_me_refuses_a_credential_that_is_not_ours(db: None) -> None:
    assert Client().get(ME, **_bearer("not-a-jwt")).status_code == 401


def test_updating_a_profile_returns_the_whole_account(token: str) -> None:
    response = Client().patch(
        PROFILE,
        {"display_name": "Zoe A.", "timezone": "Europe/Lisbon"},
        content_type="application/json",
        **_bearer(token),
    )

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["profile"]["display_name"] == "Zoe A."
    assert body["profile"]["timezone"] == "Europe/Lisbon"
    assert body["username"] == "zoe", "the account comes back too, not just the profile"


def test_an_omitted_field_is_left_alone(token: str) -> None:
    client = Client()
    client.patch(PROFILE, {"bio": "Keeps this."}, content_type="application/json", **_bearer(token))

    client.patch(
        PROFILE, {"display_name": "Zoe"}, content_type="application/json", **_bearer(token)
    )

    assert client.get(ME, **_bearer(token)).json()["profile"]["bio"] == "Keeps this."


def test_an_empty_string_clears_a_field(token: str) -> None:
    """Which is the distinction an omitted field cannot express."""
    client = Client()
    client.patch(PROFILE, {"bio": "Temporary."}, content_type="application/json", **_bearer(token))

    client.patch(PROFILE, {"bio": ""}, content_type="application/json", **_bearer(token))

    assert client.get(ME, **_bearer(token)).json()["profile"]["bio"] == ""


def test_a_date_of_birth_round_trips(token: str) -> None:
    response = Client().patch(
        PROFILE,
        {"date_of_birth": "1990-04-23"},
        content_type="application/json",
        **_bearer(token),
    )

    assert response.json()["profile"]["date_of_birth"] == "1990-04-23"


def test_a_date_that_is_not_a_date_is_refused_with_an_explanation(token: str) -> None:
    response = Client().patch(
        PROFILE,
        {"date_of_birth": "the nineties"},
        content_type="application/json",
        **_bearer(token),
    )

    assert response.status_code == 400
    assert "ISO date" in response.json()["detail"]


def test_an_empty_update_is_refused_rather_than_silently_doing_nothing(token: str) -> None:
    response = Client().patch(PROFILE, {}, content_type="application/json", **_bearer(token))

    assert response.status_code == 400


def test_updating_a_profile_needs_a_credential(db: None) -> None:
    response = Client().patch(PROFILE, {"display_name": "Nobody"}, content_type="application/json")

    assert response.status_code == 401


def test_one_account_cannot_reach_anothers_profile(db: None) -> None:
    """There is no id in the URL, which is the simplest way to guarantee it."""
    first = _password_token(Client(), "zoe")
    second = _password_token(Client(), "someone-else")

    Client().patch(
        PROFILE, {"display_name": "Mine"}, content_type="application/json", **_bearer(first)
    )

    assert Client().get(ME, **_bearer(second)).json()["profile"]["display_name"] == ""


# --- every method's token opens the same door ------------------------------


def _email_code_token(client: Client, email: str) -> str:
    started = client.post(
        "/api/v1/auth/email-code/signup/start",
        {"email": email},
        content_type="application/json",
    ).json()
    code = delivery.outbox[-1].body.rsplit(": ", 1)[1]
    response = client.post(
        "/api/v1/auth/email-code/signup/verify",
        {"ticket": started["ticket"], "code": code},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    token: str = response.json()["credentials"]["access_token"]
    return token


def _sms_code_token(client: Client, phone: str) -> str:
    started = client.post(
        "/api/v1/auth/sms-code/signup/start",
        {"phone": phone},
        content_type="application/json",
    ).json()
    code = delivery.outbox[-1].body.rsplit(": ", 1)[1]
    response = client.post(
        "/api/v1/auth/sms-code/signup/verify",
        {"ticket": started["ticket"], "code": code},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    token: str = response.json()["credentials"]["access_token"]
    return token


def _magic_link_token(client: Client, email: str) -> str:
    client.post(
        "/api/v1/auth/magic-link/signup/start",
        {"email": email},
        content_type="application/json",
    )
    link_token = delivery.outbox[-1].body.split("token=", 1)[1].split("\n", 1)[0]
    response = client.post(
        "/api/v1/auth/magic-link/verify",
        {"token": link_token},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    token: str = response.json()["credentials"]["access_token"]
    return token


@pytest.mark.parametrize(
    ("method", "make_token"),
    [
        ("password", lambda client: _password_token(client, "by-password")),
        ("email_code", lambda client: _email_code_token(client, "by-code@example.com")),
        ("sms_code", lambda client: _sms_code_token(client, "+14155550101")),
        ("magic_link", lambda client: _magic_link_token(client, "by-link@example.com")),
    ],
)
def test_a_token_from_any_method_authenticates_an_unrelated_endpoint(
    db: None, method: str, make_token: Any
) -> None:
    """No endpoint should ever need to know which method signed the caller in."""
    client = Client()

    response = client.get(ME, **_bearer(make_token(client)))

    assert response.status_code == 200, response.content
    assert response.json()["profile"]["locale"] == "en-us"


def test_a_code_login_records_that_the_address_was_reached(db: None) -> None:
    client = Client()

    token = _email_code_token(client, "by-code@example.com")

    assert client.get(ME, **_bearer(token)).json()["email_verified"] is True


def test_a_password_signup_does_not_claim_the_address_is_verified(token: str) -> None:
    """Nobody proved they can read it: they only typed it into a form."""
    assert Client().get(ME, **_bearer(token)).json()["email_verified"] is False


def test_a_phone_signup_produces_an_account_with_no_address(db: None) -> None:
    client = Client()

    token = _sms_code_token(client, "+14155550101")

    assert client.get(ME, **_bearer(token)).json()["email"] == ""


def test_every_method_lands_on_one_account_model(db: None) -> None:
    """Four different front doors, one table behind them."""
    _password_token(Client(), "by-password")
    _email_code_token(Client(), "by-code@example.com")
    _sms_code_token(Client(), "+14155550101")
    _magic_link_token(Client(), "by-link@example.com")

    assert User.objects.count() == 4
    assert all(user.profile is not None for user in User.objects.all())


def test_a_pending_second_factor_hands_back_no_token_to_use(db: None) -> None:
    """The ticket is not a credential, and must not open anything."""
    from infrastructure.auth.twofactor.models import SecondFactor, SecondFactorMethod

    token = _password_token(Client(), "zoe")
    user = User.objects.get(username="zoe")
    SecondFactor.objects.create(user=user, method=SecondFactorMethod.TOTP).confirm()
    get_challenge_store()

    response = Client().post(
        "/api/v1/auth/password/login",
        {"identifier": "zoe", "password": PASSWORD},
        content_type="application/json",
    )

    body = response.json()
    assert body["requires_second_factor"] is True
    assert body["credentials"] is None
    assert Client().get(ME, **_bearer(body["login_ticket"])).status_code == 401
    assert Client().get(ME, **_bearer(token)).status_code == 200, "the earlier token still works"
