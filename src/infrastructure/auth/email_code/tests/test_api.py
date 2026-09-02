from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from infrastructure.auth.core import delivery
from infrastructure.auth.core.models import AuthEvent, AuthEventType
from infrastructure.oauth.rotation.models import TokenFamily

SIGNUP_START = "/api/v1/auth/email-code/signup/start"
SIGNUP_VERIFY = "/api/v1/auth/email-code/signup/verify"
LOGIN_START = "/api/v1/auth/email-code/login/start"
LOGIN_VERIFY = "/api/v1/auth/email-code/login/verify"
LOGOUT = "/api/v1/auth/email-code/logout"


def _last_code() -> str:
    return delivery.outbox[-1].body.rsplit(": ", 1)[1]


def _start(client: Client, url: str, email: str = "zoe@example.com") -> str:
    response = client.post(url, {"email": email}, content_type="application/json")
    assert response.status_code == 200, response.content
    return response.json()["data"]["ticket"]


def test_signup_sends_a_code_and_creates_the_account(db: None) -> None:
    client = Client()
    ticket = _start(client, SIGNUP_START)

    assert delivery.outbox[-1].destination == "zoe@example.com"

    response = client.post(
        SIGNUP_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["data"]["credentials"]["access_token"]
    assert get_user_model()._default_manager.filter(email="zoe@example.com").exists()


def test_the_start_response_masks_the_destination(db: None) -> None:
    response = Client().post(
        LOGIN_START, {"email": "zoe@example.com"}, content_type="application/json"
    )

    assert response.json()["data"]["destination"] == "z***@example.com"


def test_the_same_response_comes_back_whether_or_not_an_account_exists(db: None) -> None:
    client = Client()
    get_user_model()._default_manager.create_user(username="zoe", email="zoe@example.com")

    known = client.post(
        LOGIN_START, {"email": "zoe@example.com"}, content_type="application/json"
    ).json()["data"]
    unknown = client.post(
        LOGIN_START, {"email": "ghost@example.com"}, content_type="application/json"
    ).json()["data"]

    assert known.keys() == unknown.keys()
    assert known["expires_in"] == unknown["expires_in"]


def test_signup_refuses_an_address_that_already_has_an_account(db: None) -> None:
    client = Client()
    get_user_model()._default_manager.create_user(username="zoe", email="zoe@example.com")
    ticket = _start(client, SIGNUP_START)

    response = client.post(
        SIGNUP_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    assert response.status_code == 409


def test_login_signs_in_an_existing_account(db: None) -> None:
    client = Client()
    user = get_user_model()._default_manager.create_user(username="zoe", email="zoe@example.com")
    ticket = _start(client, LOGIN_START)

    response = client.post(
        LOGIN_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert get_user_model()._default_manager.count() == 1
    assert user.oauth_rotation_rotatingaccesstoken_tokens.exists()


def test_login_creates_an_account_when_auto_creation_is_on(db: None) -> None:
    client = Client()
    ticket = _start(client, LOGIN_START)

    response = client.post(
        LOGIN_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert get_user_model()._default_manager.filter(email="zoe@example.com").exists()


@override_settings(AUTH_AUTO_CREATE_USERS=False)
def test_login_refuses_an_unknown_address_when_auto_creation_is_off(db: None) -> None:
    client = Client()
    ticket = _start(client, LOGIN_START)

    response = client.post(
        LOGIN_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    assert response.status_code == 404


def test_a_wrong_code_is_rejected(db: None) -> None:
    client = Client()
    ticket = _start(client, LOGIN_START)

    response = client.post(
        LOGIN_VERIFY,
        {"ticket": ticket, "code": "000000"},
        content_type="application/json",
    )

    assert response.status_code == 400


def test_a_signup_ticket_cannot_be_spent_on_login(db: None) -> None:
    client = Client()
    ticket = _start(client, SIGNUP_START)

    response = client.post(
        LOGIN_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    assert response.status_code == 400


def test_a_code_cannot_be_used_twice(db: None) -> None:
    client = Client()
    ticket = _start(client, LOGIN_START)
    code = _last_code()
    client.post(LOGIN_VERIFY, {"ticket": ticket, "code": code}, content_type="application/json")

    response = client.post(
        LOGIN_VERIFY, {"ticket": ticket, "code": code}, content_type="application/json"
    )

    assert response.status_code == 410


@override_settings(AUTH_RESEND_COOLDOWN_SECONDS=30)
def test_a_second_request_inside_the_cooldown_is_refused(db: None) -> None:
    client = Client()
    client.post(LOGIN_START, {"email": "zoe@example.com"}, content_type="application/json")

    response = client.post(
        LOGIN_START, {"email": "zoe@example.com"}, content_type="application/json"
    )

    assert response.status_code == 429
    assert response["Retry-After"] == "30"


@override_settings(AUTH_MAX_SENDS_PER_HOUR=3)
def test_the_hourly_ceiling_stops_a_flood(db: None) -> None:
    client = Client()
    statuses = [
        client.post(
            LOGIN_START, {"email": "zoe@example.com"}, content_type="application/json"
        ).status_code
        for _ in range(5)
    ]

    assert statuses.count(429) >= 1


def test_a_malformed_address_is_refused(db: None) -> None:
    response = Client().post(
        LOGIN_START, {"email": "not-an-address"}, content_type="application/json"
    )

    assert response.status_code == 400


def _credentials(client: Client) -> dict:
    ticket = _start(client, LOGIN_START)
    response = client.post(
        LOGIN_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return response.json()["data"]["credentials"]


def test_logout_revokes_the_presented_token(db: None) -> None:
    client = Client()
    credentials = _credentials(client)

    response = client.post(
        LOGOUT, {"token": credentials["access_token"]}, content_type="application/json"
    )

    assert response.status_code == 200
    assert response.json()["data"] == {"detail": "Signed out."}
    assert TokenFamily.objects.get().revoked_at is not None
    assert AuthEvent.objects.filter(event_type=AuthEventType.LOGOUT, method="email_code").exists()


def test_logout_reads_the_authorization_header(db: None) -> None:
    client = Client()
    credentials = _credentials(client)

    response = client.post(
        LOGOUT,
        {},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}",
    )

    assert response.status_code == 200
    assert TokenFamily.objects.get().revoked_at is not None


def test_logout_without_a_token_is_still_a_success(db: None) -> None:
    """Signing out is idempotent; a client clearing a stale token gets no error."""
    response = Client().post(LOGOUT, {}, content_type="application/json")

    assert response.status_code == 200
    assert AuthEvent.objects.filter(event_type=AuthEventType.LOGOUT).exists() is False


def test_logging_out_twice_does_not_fail(db: None) -> None:
    client = Client()
    credentials = _credentials(client)
    body = {"token": credentials["access_token"]}
    client.post(LOGOUT, body, content_type="application/json")

    assert client.post(LOGOUT, body, content_type="application/json").status_code == 200
