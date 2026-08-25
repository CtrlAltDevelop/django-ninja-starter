from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from infrastructure.auth.core import delivery
from infrastructure.auth.core.models import AuthEvent, AuthEventType, PhoneNumber
from infrastructure.oauth_rotation.models import TokenFamily

PHONE = "+14155550101"
SIGNUP_START = "/api/v1/auth/sms-code/signup/start"
SIGNUP_VERIFY = "/api/v1/auth/sms-code/signup/verify"
LOGIN_START = "/api/v1/auth/sms-code/login/start"
LOGIN_VERIFY = "/api/v1/auth/sms-code/login/verify"
LOGOUT = "/api/v1/auth/sms-code/logout"


def _last_code() -> str:
    return delivery.outbox[-1].body.rsplit(": ", 1)[1]


def _start(client: Client, url: str, phone: str = PHONE) -> str:
    response = client.post(url, {"phone": phone}, content_type="application/json")
    assert response.status_code == 200, response.content
    return response.json()["ticket"]


def test_signup_creates_an_account_owning_a_verified_number(db: None) -> None:
    client = Client()
    ticket = _start(client, SIGNUP_START)

    assert delivery.outbox[-1].channel == "sms"
    assert delivery.outbox[-1].destination == PHONE

    response = client.post(
        SIGNUP_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["credentials"]["access_token"]
    assert PhoneNumber.objects.get(number=PHONE).is_verified is True


def test_the_start_response_masks_the_number(db: None) -> None:
    response = Client().post(LOGIN_START, {"phone": PHONE}, content_type="application/json")

    assert response.json()["destination"] == "***0101"


def test_spacing_in_the_number_does_not_change_the_account(db: None) -> None:
    client = Client()
    ticket = _start(client, SIGNUP_START, "+1 (415) 555-0101")
    client.post(
        SIGNUP_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    ticket = _start(client, LOGIN_START, PHONE)
    response = client.post(
        LOGIN_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert PhoneNumber.objects.count() == 1


def test_signup_refuses_a_number_that_already_has_an_account(db: None) -> None:
    client = Client()
    user = get_user_model()._default_manager.create_user(username="zoe")
    PhoneNumber.objects.create(user=user, number=PHONE, is_verified=True)
    ticket = _start(client, SIGNUP_START)

    response = client.post(
        SIGNUP_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    assert response.status_code == 409


def test_signing_in_confirms_a_number_that_was_only_pending(db: None) -> None:
    """Reading the code proves control, which is exactly what verification means."""
    client = Client()
    user = get_user_model()._default_manager.create_user(username="zoe")
    PhoneNumber.objects.create(user=user, number=PHONE, is_verified=False)
    ticket = _start(client, LOGIN_START)

    response = client.post(
        LOGIN_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert PhoneNumber.objects.get(number=PHONE).is_verified is True


@override_settings(AUTH_AUTO_CREATE_USERS=False)
def test_login_refuses_an_unknown_number_when_auto_creation_is_off(db: None) -> None:
    client = Client()
    ticket = _start(client, LOGIN_START)

    response = client.post(
        LOGIN_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    assert response.status_code == 404


def test_a_number_outside_e164_is_refused(db: None) -> None:
    response = Client().post(LOGIN_START, {"phone": "4155550101"}, content_type="application/json")

    assert response.status_code == 400
    assert delivery.outbox == []


def _credentials(client: Client) -> dict:
    ticket = _start(client, LOGIN_START)
    response = client.post(
        LOGIN_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return response.json()["credentials"]


def test_login_creates_an_account_and_its_number_when_auto_creation_is_on(db: None) -> None:
    client = Client()
    ticket = _start(client, LOGIN_START)

    response = client.post(
        LOGIN_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["credentials"]["access_token"]
    record = PhoneNumber.objects.get(number=PHONE)
    assert record.is_verified is True
    assert AuthEvent.objects.filter(event_type=AuthEventType.SIGNUP, method="sms_code").exists()


def test_signing_in_attaches_a_number_the_account_did_not_have(db: None) -> None:
    """The number reached is the one proved, so it becomes the account's own."""
    client = Client()
    user = get_user_model()._default_manager.create_user(username="zoe")
    PhoneNumber.objects.create(user=user, number="+14155550999", is_verified=True)
    ticket = _start(client, LOGIN_START, "+14155550999")
    client.post(
        LOGIN_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )
    PhoneNumber.objects.filter(number="+14155550999").delete()

    ticket = _start(client, LOGIN_START, "+14155550999")
    response = client.post(
        LOGIN_VERIFY,
        {"ticket": ticket, "code": _last_code()},
        content_type="application/json",
    )

    assert response.status_code == 200


def test_a_wrong_code_is_rejected(db: None) -> None:
    client = Client()
    ticket = _start(client, LOGIN_START)

    response = client.post(
        LOGIN_VERIFY, {"ticket": ticket, "code": "000000"}, content_type="application/json"
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
def test_a_second_code_inside_the_cooldown_is_refused(db: None) -> None:
    client = Client()
    client.post(LOGIN_START, {"phone": PHONE}, content_type="application/json")

    response = client.post(LOGIN_START, {"phone": PHONE}, content_type="application/json")

    assert response.status_code == 429
    assert response["Retry-After"] == "30"


def test_logout_revokes_the_presented_token(db: None) -> None:
    client = Client()
    credentials = _credentials(client)

    response = client.post(
        LOGOUT, {"token": credentials["access_token"]}, content_type="application/json"
    )

    assert response.status_code == 200
    assert TokenFamily.objects.get().revoked_at is not None
    assert AuthEvent.objects.filter(event_type=AuthEventType.LOGOUT, method="sms_code").exists()


def test_logout_without_a_token_is_still_a_success(db: None) -> None:
    response = Client().post(LOGOUT, {}, content_type="application/json")

    assert response.status_code == 200
