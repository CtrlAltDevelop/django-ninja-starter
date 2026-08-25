from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from infrastructure.auth.core import delivery
from infrastructure.auth.core.models import AuthEvent, AuthEventType
from infrastructure.oauth_rotation.models import TokenFamily

SIGNUP_START = "/api/v1/auth/magic-link/signup/start"
LOGIN_START = "/api/v1/auth/magic-link/login/start"
VERIFY = "/api/v1/auth/magic-link/verify"
LOGOUT = "/api/v1/auth/magic-link/logout"


def _emailed_token() -> str:
    link = delivery.outbox[-1].body.split(": ", 1)[1].split("\n", 1)[0]
    return parse_qs(urlsplit(link).query)["token"][0]


def _start(client: Client, url: str, email: str = "zoe@example.com") -> str:
    response = client.post(url, {"email": email}, content_type="application/json")
    assert response.status_code == 200, response.content
    return _emailed_token()


def test_the_token_is_only_ever_in_the_email(db: None) -> None:
    """Returning it to the caller would let anyone sign in as any address."""
    response = Client().post(
        LOGIN_START, {"email": "zoe@example.com"}, content_type="application/json"
    )

    body = response.json()
    assert set(body) == {"detail", "destination", "expires_in"}
    assert _emailed_token() not in response.content.decode()


def test_the_link_points_at_the_configured_landing_page(db: None) -> None:
    Client().post(LOGIN_START, {"email": "zoe@example.com"}, content_type="application/json")

    assert "https://example.test/auth/link?token=" in delivery.outbox[-1].body


def test_following_a_signup_link_creates_the_account(db: None) -> None:
    client = Client()
    token = _start(client, SIGNUP_START)

    response = client.post(VERIFY, {"token": token}, content_type="application/json")

    assert response.status_code == 200
    assert response.json()["credentials"]["access_token"]
    assert get_user_model()._default_manager.filter(email="zoe@example.com").exists()


def test_following_a_login_link_signs_in_an_existing_account(db: None) -> None:
    client = Client()
    get_user_model()._default_manager.create_user(username="zoe", email="zoe@example.com")
    token = _start(client, LOGIN_START)

    response = client.post(VERIFY, {"token": token}, content_type="application/json")

    assert response.status_code == 200
    assert get_user_model()._default_manager.count() == 1


def test_a_link_works_only_once(db: None) -> None:
    client = Client()
    token = _start(client, LOGIN_START)
    client.post(VERIFY, {"token": token}, content_type="application/json")

    response = client.post(VERIFY, {"token": token}, content_type="application/json")

    assert response.status_code == 410


def test_an_unknown_token_is_refused(db: None) -> None:
    response = Client().post(VERIFY, {"token": "not-a-real-token"}, content_type="application/json")

    assert response.status_code == 410


@override_settings(AUTH_AUTO_CREATE_USERS=False)
def test_a_login_link_will_not_create_an_account_when_auto_creation_is_off(db: None) -> None:
    client = Client()
    token = _start(client, LOGIN_START)

    response = client.post(VERIFY, {"token": token}, content_type="application/json")

    assert response.status_code == 404


@override_settings(AUTH_AUTO_CREATE_USERS=False)
def test_a_signup_link_still_creates_an_account_when_auto_creation_is_off(db: None) -> None:
    """Auto-creation governs surprise accounts from a login, not an explicit sign-up."""
    client = Client()
    token = _start(client, SIGNUP_START)

    response = client.post(VERIFY, {"token": token}, content_type="application/json")

    assert response.status_code == 200


@override_settings(AUTH_RESEND_COOLDOWN_SECONDS=30)
def test_a_second_link_inside_the_cooldown_is_refused(db: None) -> None:
    client = Client()
    client.post(LOGIN_START, {"email": "zoe@example.com"}, content_type="application/json")

    response = client.post(
        LOGIN_START, {"email": "zoe@example.com"}, content_type="application/json"
    )

    assert response.status_code == 429


def _credentials(client: Client) -> dict:
    token = _start(client, LOGIN_START)
    response = client.post(VERIFY, {"token": token}, content_type="application/json")
    assert response.status_code == 200, response.content
    return response.json()["credentials"]


def test_signup_start_reports_where_the_link_went(db: None) -> None:
    response = Client().post(
        SIGNUP_START, {"email": "zoe@example.com"}, content_type="application/json"
    )

    assert response.status_code == 200
    assert response.json()["destination"] == "z***@example.com"
    assert delivery.outbox[-1].destination == "zoe@example.com"


def test_a_malformed_address_sends_nothing(db: None) -> None:
    response = Client().post(
        LOGIN_START, {"email": "not-an-address"}, content_type="application/json"
    )

    assert response.status_code == 400
    assert delivery.outbox == []


def test_a_signup_link_refuses_nothing_when_the_account_already_exists(db: None) -> None:
    """Proving control of the address is enough; the link signs them in."""
    client = Client()
    get_user_model()._default_manager.create_user(username="zoe", email="zoe@example.com")
    token = _start(client, SIGNUP_START)

    response = client.post(VERIFY, {"token": token}, content_type="application/json")

    assert response.status_code == 200
    assert get_user_model()._default_manager.count() == 1


def test_logout_revokes_the_presented_token(db: None) -> None:
    client = Client()
    credentials = _credentials(client)

    response = client.post(
        LOGOUT, {"token": credentials["access_token"]}, content_type="application/json"
    )

    assert response.status_code == 200
    assert TokenFamily.objects.get().revoked_at is not None
    assert AuthEvent.objects.filter(event_type=AuthEventType.LOGOUT, method="magic_link").exists()


def test_logout_without_a_token_is_still_a_success(db: None) -> None:
    response = Client().post(LOGOUT, {}, content_type="application/json")

    assert response.status_code == 200
