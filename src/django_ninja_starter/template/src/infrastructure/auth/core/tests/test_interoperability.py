"""One credential, every endpoint: what a token from any method is worth.

The apps are independent -- each installs on its own, owns its own tables and
mounts its own routes -- but the credential they issue must not be. A token from
an SMS code has to open exactly the doors a password token opens, or "pick the
methods you want" quietly becomes "pick one, because mixing them breaks".

That guarantee holds structurally: every method finishes through
``issue_credentials``, which is the only place a credential is minted. This file
is what keeps it true, by actually signing in through each method in turn and
then calling every protected endpoint in the project with what came back.

The failure this is written against is a 401 -- an endpoint refusing a caller it
should recognise. Any other status is somebody else's business: enrolling a
second factor twice is a 409 and a perfectly good outcome here, because the
request was authenticated before it was refused.
"""

from typing import Any

import pytest
from django.test import Client

from infrastructure.auth.core import delivery
from infrastructure.auth.twofactor.models import SecondFactor, SecondFactorMethod
from infrastructure.auth.twofactor.services import begin_totp
from infrastructure.oauth.core import jwt_tokens

PASSWORD = "corr3ct-horse-battery"

# Every route in the project that requires a credential, with a body that gets
# past validation. What each one decides afterwards does not matter here.
PROTECTED: list[tuple[str, str, dict[str, Any]]] = [
    ("POST", "/api/v1/auth/password/change", {"current_password": "x", "new_password": "y"}),
    ("GET", "/api/v1/auth/2fa/methods", {}),
    ("POST", "/api/v1/auth/2fa/totp/enroll", {}),
    ("POST", "/api/v1/auth/2fa/sms/enroll", {"phone": "+14155550188"}),
    ("POST", "/api/v1/auth/2fa/email/enroll", {}),
    ("POST", "/api/v1/auth/2fa/recovery/generate", {}),
    ("DELETE", "/api/v1/auth/2fa/totp", {}),
    ("GET", "/api/v1/auth/token/sessions", {}),
    ("DELETE", "/api/v1/auth/token/sessions/00000000-0000-0000-0000-000000000000", {}),
]


def _code() -> str:
    return delivery.outbox[-1].body.rsplit(": ", 1)[1]


def _post(client: Client, url: str, body: dict[str, Any]) -> Any:
    return client.post(url, body, content_type="application/json")


def _credentials(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.content
    body = response.json()["data"]
    assert body["requires_second_factor"] is False, body
    credentials: dict[str, Any] = body["credentials"]
    assert credentials["access_token"], body
    return credentials


def _via_password(client: Client, suffix: str) -> dict[str, Any]:
    return _credentials(
        _post(
            client,
            "/api/v1/auth/password/signup",
            {
                "identifier": f"zoe{suffix}",
                "password": PASSWORD,
                "email": f"zoe{suffix}@example.com",
            },
        )
    )


def _via_email_code(client: Client, suffix: str) -> dict[str, Any]:
    start = _post(
        client, "/api/v1/auth/email-code/signup/start", {"email": f"eve{suffix}@example.com"}
    )
    assert start.status_code == 200, start.content
    return _credentials(
        _post(
            client,
            "/api/v1/auth/email-code/signup/verify",
            {"ticket": start.json()["data"]["ticket"], "code": _code()},
        )
    )


def _via_sms_code(client: Client, suffix: str) -> dict[str, Any]:
    start = _post(client, "/api/v1/auth/sms-code/signup/start", {"phone": f"+1415555{suffix:0>4}"})
    assert start.status_code == 200, start.content
    return _credentials(
        _post(
            client,
            "/api/v1/auth/sms-code/signup/verify",
            {"ticket": start.json()["data"]["ticket"], "code": _code()},
        )
    )


def _via_magic_link(client: Client, suffix: str) -> dict[str, Any]:
    start = _post(
        client, "/api/v1/auth/magic-link/signup/start", {"email": f"mia{suffix}@example.com"}
    )
    assert start.status_code == 200, start.content
    token = delivery.outbox[-1].body.split("token=", 1)[1].split("\n", 1)[0].strip()
    return _credentials(_post(client, "/api/v1/auth/magic-link/verify", {"token": token}))


def _via_second_factor(client: Client, suffix: str) -> dict[str, Any]:
    """Sign in with a password, then clear a recovery-code second factor."""
    from infrastructure.auth.twofactor.services import issue_recovery_codes

    _via_password(client, suffix)
    from django.contrib.auth import get_user_model

    user = get_user_model()._default_manager.get(username=f"zoe{suffix}")
    codes = issue_recovery_codes(user)

    login = _post(
        client, "/api/v1/auth/password/login", {"identifier": f"zoe{suffix}", "password": PASSWORD}
    )
    assert login.json()["data"]["requires_second_factor"] is True, login.content
    return _credentials(
        _post(
            client,
            "/api/v1/auth/2fa/verify",
            {
                "login_ticket": login.json()["data"]["login_ticket"],
                "code": codes[0],
                "method": SecondFactorMethod.RECOVERY,
            },
        )
    )


METHODS = {
    "password": _via_password,
    "email_code": _via_email_code,
    "sms_code": _via_sms_code,
    "magic_link": _via_magic_link,
    "password+recovery": _via_second_factor,
}


@pytest.mark.parametrize("method", sorted(METHODS))
def test_every_method_issues_a_credential_of_the_same_shape(db: None, method: str) -> None:
    """A client written against one method already understands the others."""
    credentials = METHODS[method](Client(), "1")

    assert credentials["token_type"] == "bearer"
    assert credentials["access_token"]
    assert credentials["refresh_token"]
    assert credentials["expires_in"] > 0
    assert credentials["session_id"]


@pytest.mark.parametrize("method", sorted(METHODS))
def test_every_methods_token_is_accepted_by_every_protected_endpoint(db: None, method: str) -> None:
    """The guarantee this file exists for."""
    client = Client()
    header = {"HTTP_AUTHORIZATION": f"Bearer {METHODS[method](client, '2')['access_token']}"}

    refused = [
        f"{verb} {path}"
        for verb, path, body in PROTECTED
        if getattr(client, verb.lower())(
            path, body, content_type="application/json", **header
        ).status_code
        == 401
    ]

    assert refused == []


@pytest.mark.parametrize("method", sorted(METHODS))
def test_a_token_is_refused_everywhere_once_its_session_ends(db: None, method: str) -> None:
    """The other half: one revocation closes every door, not just the one it came from."""
    client = Client()
    credentials = METHODS[method](client, "3")
    header = {"HTTP_AUTHORIZATION": f"Bearer {credentials['access_token']}"}
    ended = client.delete(f"/api/v1/auth/token/sessions/{credentials['session_id']}", **header)
    assert ended.status_code == 200, ended.content

    accepted = [
        f"{verb} {path}"
        for verb, path, body in PROTECTED
        if getattr(client, verb.lower())(
            path, body, content_type="application/json", **header
        ).status_code
        != 401
    ]

    assert accepted == []


@pytest.mark.parametrize("method", sorted(METHODS))
def test_every_methods_token_names_the_method_that_minted_it(db: None, method: str) -> None:
    """`amr` is what lets a service require a stronger method for a stronger action."""
    credentials = METHODS[method](Client(), "4")

    claims = jwt_tokens.decode(credentials["access_token"], token_type=jwt_tokens.ACCESS)

    assert claims.methods
    assert claims.mode == "rotation"


@pytest.mark.parametrize("method", sorted(METHODS))
def test_every_methods_refresh_token_works_at_the_shared_token_endpoint(
    db: None, method: str
) -> None:
    """One refresh endpoint, whichever method the credential came from."""
    client = Client()
    credentials = METHODS[method](client, "5")

    refreshed = _post(
        client, "/api/v1/auth/token/refresh", {"refresh_token": credentials["refresh_token"]}
    )

    assert refreshed.status_code == 200, refreshed.content
    body = refreshed.json()["data"]
    assert body["access_token"] != credentials["access_token"]
    assert body["session_id"] == credentials["session_id"]

    assert (
        client.get(
            "/api/v1/auth/token/sessions",
            HTTP_AUTHORIZATION=f"Bearer {body['access_token']}",
        ).status_code
        == 200
    )


def test_one_account_can_hold_credentials_from_several_methods_at_once(db: None) -> None:
    """Adding a second way in must not invalidate the first.

    A user who signs up with a password and later uses a magic link on their
    phone should end with two live sessions, not one that displaced the other.
    """
    from django.contrib.auth import get_user_model

    client = Client()
    password_credentials = _via_password(client, "6")
    user = get_user_model()._default_manager.get(username="zoe6")
    user.email = "shared@example.com"
    user.save(update_fields=["email"])

    start = _post(client, "/api/v1/auth/magic-link/login/start", {"email": "shared@example.com"})
    assert start.status_code == 200, start.content
    token = delivery.outbox[-1].body.split("token=", 1)[1].split("\n", 1)[0].strip()
    link_credentials = _credentials(
        _post(client, "/api/v1/auth/magic-link/verify", {"token": token})
    )

    assert link_credentials["session_id"] != password_credentials["session_id"]
    for credentials in (password_credentials, link_credentials):
        assert (
            client.get(
                "/api/v1/auth/token/sessions",
                HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}",
            ).status_code
            == 200
        )


def test_a_second_factor_enrolled_through_one_method_gates_all_of_them(db: None) -> None:
    """Otherwise a user could enable 2FA and still be let in by the weakest route."""
    from django.contrib.auth import get_user_model

    client = Client()
    _via_password(client, "7")
    user = get_user_model()._default_manager.get(username="zoe7")
    user.email = "gated@example.com"
    user.save(update_fields=["email"])
    factor, _, _ = begin_totp(user)
    factor.confirm()

    for url, body in (
        ("/api/v1/auth/password/login", {"identifier": "zoe7", "password": PASSWORD}),
        ("/api/v1/auth/email-code/login/start", {"email": "gated@example.com"}),
    ):
        response = _post(client, url, body)
        assert response.status_code == 200, response.content

    verify = _post(
        client,
        "/api/v1/auth/email-code/login/verify",
        {"ticket": response.json()["data"]["ticket"], "code": _code()},
    )

    assert verify.status_code == 200, verify.content
    assert verify.json()["data"]["requires_second_factor"] is True
    assert verify.json()["data"]["credentials"] is None
    assert SecondFactor.objects.filter(user=user, confirmed_at__isnull=False).count() == 1
