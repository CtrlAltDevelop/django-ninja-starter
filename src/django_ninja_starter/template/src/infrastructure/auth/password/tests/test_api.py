from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from infrastructure.auth.core import delivery
from infrastructure.auth.core.models import AuthEvent
from infrastructure.oauth.rotation.models import RotatingAccessToken, TokenFamily

PASSWORD = "corr3ct-horse-battery"
SIGNUP = "/api/v1/auth/password/signup"
LOGIN = "/api/v1/auth/password/login"


def _signup(client: Client, identifier: str = "zoe", email: str = "zoe@example.com") -> dict:
    response = client.post(
        SIGNUP,
        {"identifier": identifier, "password": PASSWORD, "email": email},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return response.json()


def test_signup_returns_a_usable_credential(db: None) -> None:
    body = _signup(Client())

    assert body["requires_second_factor"] is False
    assert body["credentials"]["token_type"] == "bearer"
    assert body["credentials"]["access_token"]
    assert body["credentials"]["refresh_token"]
    assert get_user_model()._default_manager.filter(username="zoe").exists()


def test_signup_refuses_a_weak_password(db: None) -> None:
    response = Client().post(
        SIGNUP,
        {"identifier": "zoe", "password": "1234"},
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "detail" in response.json()


def test_signup_refuses_a_taken_username(db: None) -> None:
    client = Client()
    _signup(client)

    response = client.post(
        SIGNUP,
        {"identifier": "zoe", "password": PASSWORD, "email": "other@example.com"},
        content_type="application/json",
    )

    assert response.status_code == 409


def test_login_accepts_the_username_or_the_email(db: None) -> None:
    client = Client()
    _signup(client)

    for identifier in ("zoe", "zoe@example.com"):
        response = client.post(
            LOGIN,
            {"identifier": identifier, "password": PASSWORD},
            content_type="application/json",
        )

        assert response.status_code == 200, identifier
        assert response.json()["credentials"]["access_token"]


def test_login_rejects_a_wrong_password(db: None) -> None:
    client = Client()
    _signup(client)

    response = client.post(
        LOGIN,
        {"identifier": "zoe", "password": "not-the-password"},
        content_type="application/json",
    )

    assert response.status_code == 401
    assert AuthEvent.objects.filter(event_type=AuthEvent.EventType.LOGIN_FAILED).exists()


def test_an_unknown_account_answers_like_a_wrong_password(db: None) -> None:
    response = Client().post(
        LOGIN,
        {"identifier": "ghost", "password": PASSWORD},
        content_type="application/json",
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Those credentials are not valid."


def test_a_disabled_account_cannot_sign_in(db: None) -> None:
    client = Client()
    _signup(client)
    user = get_user_model()._default_manager.get(username="zoe")
    user.is_active = False
    user.save(update_fields=["is_active"])

    response = client.post(
        LOGIN,
        {"identifier": "zoe", "password": PASSWORD},
        content_type="application/json",
    )

    assert response.status_code == 403


@override_settings(AUTH_MAX_SENDS_PER_HOUR=100)
def test_repeated_failures_are_throttled(db: None) -> None:
    client = Client()
    _signup(client)

    statuses = [
        client.post(
            LOGIN,
            {"identifier": "zoe", "password": "wrong"},
            content_type="application/json",
        ).status_code
        for _ in range(12)
    ]

    assert 429 in statuses


def test_logout_revokes_the_presented_token(db: None) -> None:
    client = Client()
    credentials = _signup(client)["credentials"]

    response = client.post(
        "/api/v1/auth/password/logout",
        {"token": credentials["access_token"]},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert TokenFamily.objects.get().revoked_at is not None


def test_logout_without_a_token_still_succeeds(db: None) -> None:
    response = Client().post("/api/v1/auth/password/logout", {}, content_type="application/json")

    assert response.status_code == 200


def test_reset_replaces_the_password_and_kills_live_tokens(db: None) -> None:
    client = Client()
    _signup(client)

    forgot = client.post(
        "/api/v1/auth/password/forgot",
        {"email": "zoe@example.com"},
        content_type="application/json",
    )
    assert forgot.status_code == 200
    ticket = forgot.json()["ticket"]
    code = delivery.outbox[-1].body.rsplit(": ", 1)[1]

    new_password = "another-g00d-secret"
    response = client.post(
        "/api/v1/auth/password/reset",
        {"ticket": ticket, "code": code, "password": new_password},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert RotatingAccessToken.objects.filter(revoked_at__isnull=True).count() == 0
    signed_in = client.post(
        LOGIN,
        {"identifier": "zoe", "password": new_password},
        content_type="application/json",
    )
    assert signed_in.status_code == 200


def test_forgot_answers_the_same_for_an_unknown_address(db: None) -> None:
    """The response must not become an oracle for which addresses are registered."""
    client = Client()
    _signup(client)

    known = client.post(
        "/api/v1/auth/password/forgot",
        {"email": "zoe@example.com"},
        content_type="application/json",
    ).json()
    unknown = client.post(
        "/api/v1/auth/password/forgot",
        {"email": "ghost@example.com"},
        content_type="application/json",
    ).json()

    assert known.keys() == unknown.keys()
    assert known["detail"] == unknown["detail"]
    assert unknown["ticket"]


def test_a_decoy_reset_ticket_cannot_set_a_password(db: None) -> None:
    client = Client()
    ticket = client.post(
        "/api/v1/auth/password/forgot",
        {"email": "ghost@example.com"},
        content_type="application/json",
    ).json()["ticket"]

    for code in (f"{index:06d}" for index in range(5)):
        response = client.post(
            "/api/v1/auth/password/reset",
            {"ticket": ticket, "code": code, "password": "another-g00d-secret"},
            content_type="application/json",
        )
        assert response.status_code in {400, 429}


def test_change_requires_the_current_password(db: None) -> None:
    client = Client()
    credentials = _signup(client)["credentials"]
    headers = {"HTTP_AUTHORIZATION": f"Bearer {credentials['access_token']}"}

    response = client.post(
        "/api/v1/auth/password/change",
        {"current_password": "wrong", "new_password": "another-g00d-secret"},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 401


def test_change_updates_the_password_and_revokes_tokens(db: None) -> None:
    client = Client()
    credentials = _signup(client)["credentials"]
    headers = {"HTTP_AUTHORIZATION": f"Bearer {credentials['access_token']}"}

    response = client.post(
        "/api/v1/auth/password/change",
        {"current_password": PASSWORD, "new_password": "another-g00d-secret"},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    assert TokenFamily.objects.get().revoked_at is not None
    assert client.get("/api/v1/auth/2fa/methods", **headers).status_code == 401, (
        "the revoked token must stop authenticating"
    )


def test_change_needs_a_signed_in_caller(db: None) -> None:
    response = Client().post(
        "/api/v1/auth/password/change",
        {"current_password": PASSWORD, "new_password": "another-g00d-secret"},
        content_type="application/json",
    )

    assert response.status_code == 401


def test_signup_refuses_a_blank_identifier(db: None) -> None:
    response = Client().post(
        SIGNUP, {"identifier": "   ", "password": PASSWORD}, content_type="application/json"
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Enter a username."


def test_signup_refuses_a_malformed_email(db: None) -> None:
    response = Client().post(
        SIGNUP,
        {"identifier": "zoe", "password": PASSWORD, "email": "not-an-address"},
        content_type="application/json",
    )

    assert response.status_code == 400


def test_login_treats_an_ambiguous_email_as_a_failed_login(db: None) -> None:
    """Two accounts share the address, so no single one can be signed in."""
    user_model = get_user_model()
    for name in ("a", "b"):
        user_model._default_manager.create_user(
            username=name, email="shared@example.com", password=PASSWORD
        )

    response = Client().post(
        LOGIN,
        {"identifier": "shared@example.com", "password": PASSWORD},
        content_type="application/json",
    )

    assert response.status_code == 401


def test_forgot_treats_an_ambiguous_email_like_an_unknown_one(db: None) -> None:
    user_model = get_user_model()
    for name in ("a", "b"):
        user_model._default_manager.create_user(name, email="shared@example.com")

    response = Client().post(
        "/api/v1/auth/password/forgot",
        {"email": "shared@example.com"},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["ticket"]
    assert delivery.outbox == [], "no code may be sent when the account is ambiguous"


def test_forgot_refuses_a_malformed_address(db: None) -> None:
    response = Client().post(
        "/api/v1/auth/password/forgot",
        {"email": "not-an-address"},
        content_type="application/json",
    )

    assert response.status_code == 400


def test_reset_enforces_the_password_policy(db: None) -> None:
    client = Client()
    _signup(client)
    forgot = client.post(
        "/api/v1/auth/password/forgot",
        {"email": "zoe@example.com"},
        content_type="application/json",
    )
    ticket = forgot.json()["ticket"]
    code = delivery.outbox[-1].body.rsplit(": ", 1)[1]

    response = client.post(
        "/api/v1/auth/password/reset",
        {"ticket": ticket, "code": code, "password": "1234"},
        content_type="application/json",
    )

    assert response.status_code == 400


def test_reset_rejects_a_wrong_code(db: None) -> None:
    client = Client()
    _signup(client)
    ticket = client.post(
        "/api/v1/auth/password/forgot",
        {"email": "zoe@example.com"},
        content_type="application/json",
    ).json()["ticket"]

    response = client.post(
        "/api/v1/auth/password/reset",
        {"ticket": ticket, "code": "000000", "password": "another-g00d-secret"},
        content_type="application/json",
    )

    assert response.status_code == 400


def test_reset_rejects_an_unknown_ticket(db: None) -> None:
    response = Client().post(
        "/api/v1/auth/password/reset",
        {"ticket": "never-issued", "code": "000000", "password": "another-g00d-secret"},
        content_type="application/json",
    )

    assert response.status_code == 410


def test_change_enforces_the_password_policy(db: None) -> None:
    client = Client()
    credentials = _signup(client)["credentials"]

    response = client.post(
        "/api/v1/auth/password/change",
        {"current_password": PASSWORD, "new_password": "1234"},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}",
    )

    assert response.status_code == 400


def test_logout_reads_the_authorization_header(db: None) -> None:
    client = Client()
    credentials = _signup(client)["credentials"]

    response = client.post(
        "/api/v1/auth/password/logout",
        {},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}",
    )

    assert response.status_code == 200
    assert TokenFamily.objects.get().revoked_at is not None
