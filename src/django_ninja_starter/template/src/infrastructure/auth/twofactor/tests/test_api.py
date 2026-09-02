import time

import pyotp
import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings

from infrastructure.auth.core import delivery
from infrastructure.auth.core.models import PhoneNumber
from infrastructure.auth.twofactor.models import (
    RecoveryCode,
    SecondFactor,
    SecondFactorMethod,
)
from infrastructure.auth.twofactor.services import TOTP_PERIOD, send_factor_code

PASSWORD = "corr3ct-horse-battery"
PHONE = "+14155550101"
SIGNUP = "/api/v1/auth/password/signup"
LOGIN = "/api/v1/auth/password/login"
VERIFY = "/api/v1/auth/2fa/verify"
CHALLENGE = "/api/v1/auth/2fa/challenge"


def _totp_code(secret: str, steps_ahead: int = 0) -> str:
    """Return a code for a chosen time step, so a test can move past a used one."""
    counter = int(time.time()) // TOTP_PERIOD + steps_ahead
    return pyotp.TOTP(secret, interval=TOTP_PERIOD).at(counter * TOTP_PERIOD)


def _register(client: Client) -> dict[str, str]:
    response = client.post(
        SIGNUP,
        {"identifier": "zoe", "password": PASSWORD, "email": "zoe@example.com"},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    token = response.json()["data"]["credentials"]["access_token"]
    return {"HTTP_AUTHORIZATION": f"Bearer {token}"}


def _enroll_totp(client: Client, headers: dict[str, str]) -> str:
    enrolled = client.post("/api/v1/auth/2fa/totp/enroll", **headers)
    assert enrolled.status_code == 200, enrolled.content
    secret = enrolled.json()["data"]["secret"]
    confirmed = client.post(
        "/api/v1/auth/2fa/totp/confirm",
        {"code": _totp_code(secret)},
        content_type="application/json",
        **headers,
    )
    assert confirmed.status_code == 200, confirmed.content
    return secret


def _sign_in(client: Client) -> dict:
    response = client.post(
        LOGIN,
        {"identifier": "zoe", "password": PASSWORD},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return response.json()["data"]


def test_enrolment_is_not_real_until_a_code_confirms_it(db: None) -> None:
    client = Client()
    headers = _register(client)
    client.post("/api/v1/auth/2fa/totp/enroll", **headers)

    assert _sign_in(client)["requires_second_factor"] is False, (
        "an unconfirmed secret must not be able to lock the account out"
    )


def test_a_confirmed_factor_turns_login_into_two_steps(db: None) -> None:
    client = Client()
    headers = _register(client)
    secret = _enroll_totp(client, headers)

    first_step = _sign_in(client)

    assert first_step["requires_second_factor"] is True
    assert first_step["credentials"] is None
    assert first_step["methods"] == ["totp"]
    assert first_step["login_ticket"]

    second_step = client.post(
        VERIFY,
        {"login_ticket": first_step["login_ticket"], "code": _totp_code(secret, 1)},
        content_type="application/json",
    )

    assert second_step.status_code == 200
    assert second_step.json()["data"]["credentials"]["access_token"]


def test_an_authenticator_code_cannot_be_used_twice(db: None) -> None:
    client = Client()
    headers = _register(client)
    secret = _enroll_totp(client, headers)
    code = _totp_code(secret, 1)
    ticket = _sign_in(client)["login_ticket"]
    client.post(VERIFY, {"login_ticket": ticket, "code": code}, content_type="application/json")

    ticket = _sign_in(client)["login_ticket"]
    response = client.post(
        VERIFY, {"login_ticket": ticket, "code": code}, content_type="application/json"
    )

    assert response.status_code == 400


def test_a_mistyped_code_does_not_throw_the_user_back_to_the_password(db: None) -> None:
    """The whole reason the pending ticket is read rather than consumed."""
    client = Client()
    headers = _register(client)
    secret = _enroll_totp(client, headers)
    ticket = _sign_in(client)["login_ticket"]

    wrong = client.post(
        VERIFY, {"login_ticket": ticket, "code": "000000"}, content_type="application/json"
    )
    assert wrong.status_code == 400

    right = client.post(
        VERIFY,
        {"login_ticket": ticket, "code": _totp_code(secret, 1)},
        content_type="application/json",
    )

    assert right.status_code == 200


@override_settings(AUTH_CHALLENGE_MAX_ATTEMPTS=3)
def test_guessing_at_the_second_factor_retires_the_pending_ticket(db: None) -> None:
    client = Client()
    headers = _register(client)
    secret = _enroll_totp(client, headers)
    ticket = _sign_in(client)["login_ticket"]

    for _ in range(4):
        client.post(
            VERIFY, {"login_ticket": ticket, "code": "000000"}, content_type="application/json"
        )

    response = client.post(
        VERIFY,
        {"login_ticket": ticket, "code": _totp_code(secret, 1)},
        content_type="application/json",
    )

    assert response.status_code == 410


def test_the_pending_ticket_alone_does_not_sign_anyone_in(db: None) -> None:
    client = Client()
    headers = _register(client)
    _enroll_totp(client, headers)
    ticket = _sign_in(client)["login_ticket"]

    response = client.post(
        VERIFY, {"login_ticket": ticket, "code": ""}, content_type="application/json"
    )

    assert response.status_code == 400


def test_an_sms_second_factor_sends_a_code_and_accepts_it(db: None) -> None:
    client = Client()
    headers = _register(client)
    enrolled = client.post(
        "/api/v1/auth/2fa/sms/enroll",
        {"phone": PHONE},
        content_type="application/json",
        **headers,
    )
    assert enrolled.status_code == 200, enrolled.content
    confirmed = client.post(
        "/api/v1/auth/2fa/sms/confirm",
        {
            "ticket": enrolled.json()["data"]["ticket"],
            "code": delivery.outbox[-1].body.rsplit(" ", 1)[1].rstrip("."),
        },
        content_type="application/json",
        **headers,
    )
    assert confirmed.status_code == 200, confirmed.content
    assert PhoneNumber.objects.get(number=PHONE).is_verified is True

    ticket = _sign_in(client)["login_ticket"]
    sent = client.post(
        CHALLENGE,
        {"login_ticket": ticket, "method": "sms"},
        content_type="application/json",
    )
    assert sent.status_code == 200, sent.content
    assert sent.json()["data"]["destination"] == "***0101"
    assert sent.json()["data"]["ticket"] == ticket, "the client should keep using one ticket"

    code = delivery.outbox[-1].body.rsplit(" ", 1)[1].rstrip(".")
    response = client.post(
        VERIFY, {"login_ticket": ticket, "code": code}, content_type="application/json"
    )

    assert response.status_code == 200
    assert response.json()["data"]["credentials"]["access_token"]


def test_a_sent_code_must_be_requested_before_it_is_verified(db: None) -> None:
    client = Client()
    headers = _register(client)
    factor = SecondFactor.objects.create(
        user=get_user_model()._default_manager.get(username="zoe"),
        method=SecondFactor.Method.EMAIL,
        destination="zoe@example.com",
    )
    factor.confirm()
    del headers
    ticket = _sign_in(client)["login_ticket"]

    response = client.post(
        VERIFY,
        {"login_ticket": ticket, "code": "123456", "method": "email"},
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "Request a code" in response.json()["description"]


def test_recovery_codes_work_once_each(db: None) -> None:
    client = Client()
    headers = _register(client)
    _enroll_totp(client, headers)
    generated = client.post("/api/v1/auth/2fa/recovery/generate", **headers)
    assert generated.status_code == 200, generated.content
    codes = generated.json()["data"]["codes"]
    assert len(codes) == 10

    ticket = _sign_in(client)["login_ticket"]
    first = client.post(
        VERIFY,
        {"login_ticket": ticket, "code": codes[0], "method": "recovery"},
        content_type="application/json",
    )
    assert first.status_code == 200

    ticket = _sign_in(client)["login_ticket"]
    second = client.post(
        VERIFY,
        {"login_ticket": ticket, "code": codes[0], "method": "recovery"},
        content_type="application/json",
    )

    assert second.status_code == 400
    assert RecoveryCode.objects.filter(used_at__isnull=True).count() == 9


def test_recovery_codes_are_stored_only_as_digests(db: None) -> None:
    client = Client()
    headers = _register(client)
    codes = client.post("/api/v1/auth/2fa/recovery/generate", **headers).json()["data"]["codes"]

    stored = set(RecoveryCode.objects.values_list("code_hash", flat=True))
    assert stored.isdisjoint(set(codes))


def test_recovery_is_never_chosen_by_inference(db: None) -> None:
    """A mistyped authenticator code must not silently burn a printed code."""
    client = Client()
    headers = _register(client)
    _enroll_totp(client, headers)
    codes = client.post("/api/v1/auth/2fa/recovery/generate", **headers).json()["data"]["codes"]
    ticket = _sign_in(client)["login_ticket"]

    response = client.post(
        VERIFY, {"login_ticket": ticket, "code": codes[0]}, content_type="application/json"
    )

    assert response.status_code == 400
    assert RecoveryCode.objects.filter(used_at__isnull=True).count() == 10


def test_regenerating_recovery_codes_retires_the_old_set(db: None) -> None:
    client = Client()
    headers = _register(client)
    first = client.post("/api/v1/auth/2fa/recovery/generate", **headers).json()["data"]["codes"]
    client.post("/api/v1/auth/2fa/recovery/generate", **headers)
    ticket = _sign_in(client)["login_ticket"]

    response = client.post(
        VERIFY,
        {"login_ticket": ticket, "code": first[0], "method": "recovery"},
        content_type="application/json",
    )

    assert response.status_code == 400


def test_methods_lists_what_is_enrolled_and_masks_destinations(db: None) -> None:
    client = Client()
    headers = _register(client)
    _enroll_totp(client, headers)
    client.post("/api/v1/auth/2fa/email/enroll", content_type="application/json", **headers)

    body = client.get("/api/v1/auth/2fa/methods", **headers).json()["data"]

    listed = {item["method"]: item for item in body["methods"]}
    assert listed["totp"]["confirmed"] is True
    assert listed["email"]["confirmed"] is False
    assert listed["email"]["destination"] == "z***@example.com"
    assert set(body["available"]) == {"totp", "sms", "email", "recovery"}


def test_removing_a_factor_returns_login_to_one_step(db: None) -> None:
    client = Client()
    headers = _register(client)
    _enroll_totp(client, headers)
    assert _sign_in(client)["requires_second_factor"] is True

    removed = client.delete("/api/v1/auth/2fa/totp", **headers)

    assert removed.status_code == 200
    assert _sign_in(client)["requires_second_factor"] is False


def test_removing_a_factor_that_is_not_set_up_is_a_404(db: None) -> None:
    client = Client()
    headers = _register(client)

    assert client.delete("/api/v1/auth/2fa/sms", **headers).status_code == 404


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("get", "/api/v1/auth/2fa/methods"),
        ("post", "/api/v1/auth/2fa/totp/enroll"),
        ("post", "/api/v1/auth/2fa/recovery/generate"),
        ("delete", "/api/v1/auth/2fa/totp"),
    ],
)
def test_managing_factors_needs_a_signed_in_caller(db: None, method: str, url: str) -> None:
    assert getattr(Client(), method)(url).status_code == 401


@override_settings(AUTH_SECOND_FACTORS=["totp"])
def test_a_factor_the_deployment_disabled_cannot_be_enrolled(db: None) -> None:
    client = Client()
    headers = _register(client)

    response = client.post(
        "/api/v1/auth/2fa/sms/enroll",
        {"phone": PHONE},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 404


def test_a_number_belonging_to_someone_else_cannot_be_enrolled(db: None) -> None:
    other = get_user_model()._default_manager.create_user(username="other")
    PhoneNumber.objects.create(user=other, number=PHONE, is_verified=True)
    client = Client()
    headers = _register(client)

    response = client.post(
        "/api/v1/auth/2fa/sms/enroll",
        {"phone": PHONE},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 409


def _enroll_email(client: Client, headers: dict[str, str]) -> None:
    enrolled = client.post("/api/v1/auth/2fa/email/enroll", **headers)
    assert enrolled.status_code == 200, enrolled.content
    confirmed = client.post(
        "/api/v1/auth/2fa/email/confirm",
        {"ticket": enrolled.json()["data"]["ticket"], "code": _sent_code()},
        content_type="application/json",
        **headers,
    )
    assert confirmed.status_code == 200, confirmed.content


def _sent_code() -> str:
    return delivery.outbox[-1].body.rsplit(" ", 1)[1].rstrip(".")


def test_an_email_second_factor_sends_a_code_and_accepts_it(db: None) -> None:
    client = Client()
    headers = _register(client)
    _enroll_email(client, headers)

    assert delivery.outbox[-1].destination == "zoe@example.com"

    ticket = _sign_in(client)["login_ticket"]
    sent = client.post(
        CHALLENGE,
        {"login_ticket": ticket, "method": "email"},
        content_type="application/json",
    )
    assert sent.status_code == 200, sent.content
    assert sent.json()["data"]["destination"] == "z***@example.com"
    assert sent.json()["data"]["channel"] == "email"

    response = client.post(
        VERIFY,
        {"login_ticket": ticket, "code": _sent_code()},
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json()["data"]["credentials"]["access_token"]


def test_email_enrolment_needs_an_address_on_the_account(db: None) -> None:
    client = Client()
    response = client.post(
        SIGNUP,
        {"identifier": "noaddress", "password": PASSWORD},
        content_type="application/json",
    )
    token = response.json()["data"]["credentials"]["access_token"]

    enrolled = client.post("/api/v1/auth/2fa/email/enroll", HTTP_AUTHORIZATION=f"Bearer {token}")

    assert enrolled.status_code == 400


def test_confirming_email_before_enrolling_is_refused(db: None) -> None:
    client = Client()
    headers = _register(client)

    response = client.post(
        "/api/v1/auth/2fa/email/confirm",
        {"ticket": "never-issued", "code": "000000"},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 410


def test_confirming_sms_before_enrolling_is_refused(db: None) -> None:
    client = Client()
    headers = _register(client)

    response = client.post(
        "/api/v1/auth/2fa/sms/confirm",
        {"ticket": "never-issued", "code": "000000"},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 410


def test_confirming_totp_before_enrolling_is_refused(db: None) -> None:
    client = Client()
    headers = _register(client)

    response = client.post(
        "/api/v1/auth/2fa/totp/confirm",
        {"code": "000000"},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 400
    assert "Start authenticator enrolment first." in response.json()["description"]


def test_confirming_totp_with_a_wrong_code_is_refused(db: None) -> None:
    client = Client()
    headers = _register(client)
    client.post("/api/v1/auth/2fa/totp/enroll", **headers)

    response = client.post(
        "/api/v1/auth/2fa/totp/confirm",
        {"code": "000000"},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 400
    assert _sign_in(client)["requires_second_factor"] is False


def test_enrolling_totp_twice_is_refused(db: None) -> None:
    client = Client()
    headers = _register(client)
    _enroll_totp(client, headers)

    response = client.post("/api/v1/auth/2fa/totp/enroll", **headers)

    assert response.status_code == 409


def test_the_factor_must_be_named_when_two_could_be_meant(db: None) -> None:
    """SMS and email both need a sent code, so neither can be assumed."""
    client = Client()
    headers = _register(client)
    _enroll_email(client, headers)
    enrolled = client.post(
        "/api/v1/auth/2fa/sms/enroll",
        {"phone": PHONE},
        content_type="application/json",
        **headers,
    )
    client.post(
        "/api/v1/auth/2fa/sms/confirm",
        {"ticket": enrolled.json()["data"]["ticket"], "code": _sent_code()},
        content_type="application/json",
        **headers,
    )
    ticket = _sign_in(client)["login_ticket"]

    response = client.post(
        VERIFY, {"login_ticket": ticket, "code": "000000"}, content_type="application/json"
    )

    assert response.status_code == 400
    assert "Specify which second factor" in response.json()["description"]


def test_a_single_enrolled_factor_needs_no_naming(db: None) -> None:
    client = Client()
    headers = _register(client)
    _enroll_email(client, headers)
    ticket = _sign_in(client)["login_ticket"]
    client.post(
        CHALLENGE, {"login_ticket": ticket, "method": "email"}, content_type="application/json"
    )

    response = client.post(
        VERIFY,
        {"login_ticket": ticket, "code": _sent_code()},
        content_type="application/json",
    )

    assert response.status_code == 200


def test_a_factor_the_account_has_not_enrolled_is_refused(db: None) -> None:
    client = Client()
    headers = _register(client)
    _enroll_totp(client, headers)
    ticket = _sign_in(client)["login_ticket"]

    response = client.post(
        VERIFY,
        {"login_ticket": ticket, "code": "000000", "method": "sms"},
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "not set up" in response.json()["description"]


def test_challenging_a_factor_the_account_lacks_is_refused(db: None) -> None:
    client = Client()
    headers = _register(client)
    _enroll_totp(client, headers)
    ticket = _sign_in(client)["login_ticket"]

    response = client.post(
        CHALLENGE, {"login_ticket": ticket, "method": "sms"}, content_type="application/json"
    )

    assert response.status_code == 400


def test_challenging_with_an_unknown_ticket_is_refused(db: None) -> None:
    response = Client().post(
        CHALLENGE,
        {"login_ticket": "never-issued", "method": "sms"},
        content_type="application/json",
    )

    assert response.status_code == 410


def test_verifying_with_an_unknown_ticket_is_refused(db: None) -> None:
    response = Client().post(
        VERIFY,
        {"login_ticket": "never-issued", "code": "000000"},
        content_type="application/json",
    )

    assert response.status_code == 410


def test_a_pending_ticket_is_void_once_the_account_is_disabled(db: None) -> None:
    client = Client()
    headers = _register(client)
    _enroll_totp(client, headers)
    ticket = _sign_in(client)["login_ticket"]
    user = get_user_model()._default_manager.get(username="zoe")
    user.is_active = False
    user.save(update_fields=["is_active"])

    response = client.post(
        VERIFY, {"login_ticket": ticket, "code": "000000"}, content_type="application/json"
    )

    assert response.status_code == 400
    assert "no longer valid" in response.json()["description"]


def test_recovery_codes_can_be_the_only_factor(db: None) -> None:
    client = Client()
    headers = _register(client)
    codes = client.post("/api/v1/auth/2fa/recovery/generate", **headers).json()["data"]["codes"]
    first_step = _sign_in(client)

    assert first_step["methods"] == ["recovery"]

    response = client.post(
        VERIFY,
        {"login_ticket": first_step["login_ticket"], "code": codes[0], "method": "recovery"},
        content_type="application/json",
    )

    assert response.status_code == 200


def test_recovery_codes_are_case_and_dash_insensitive(db: None) -> None:
    client = Client()
    headers = _register(client)
    codes = client.post("/api/v1/auth/2fa/recovery/generate", **headers).json()["data"]["codes"]
    typed = codes[0].lower().replace("-", " ")
    ticket = _sign_in(client)["login_ticket"]

    response = client.post(
        VERIFY,
        {"login_ticket": ticket, "code": typed, "method": "recovery"},
        content_type="application/json",
    )

    assert response.status_code == 200


def test_removing_recovery_discards_the_stored_codes(db: None) -> None:
    client = Client()
    headers = _register(client)
    client.post("/api/v1/auth/2fa/recovery/generate", **headers)

    client.delete("/api/v1/auth/2fa/recovery", **headers)

    assert RecoveryCode.objects.count() == 0


def test_methods_reports_the_remaining_recovery_codes(db: None) -> None:
    client = Client()
    headers = _register(client)
    client.post("/api/v1/auth/2fa/recovery/generate", **headers)

    body = client.get("/api/v1/auth/2fa/methods", **headers).json()["data"]

    assert body["unused_recovery_codes"] == 10


@override_settings(AUTH_SECOND_FACTORS=["sms"])
def test_a_disabled_factor_cannot_be_confirmed(db: None) -> None:
    client = Client()
    headers = _register(client)

    response = client.post(
        "/api/v1/auth/2fa/totp/confirm",
        {"code": "000000"},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 404


@override_settings(AUTH_SECOND_FACTORS=["totp"])
def test_recovery_generation_respects_the_enabled_list(db: None) -> None:
    client = Client()
    headers = _register(client)

    assert client.post("/api/v1/auth/2fa/recovery/generate", **headers).status_code == 404


def test_a_code_minted_for_another_account_is_refused(db: None) -> None:
    """The ticket is valid, but it does not belong to the caller."""
    other = get_user_model()._default_manager.create_user(
        username="other", email="other@example.com"
    )
    factor = SecondFactor.objects.create(
        user=other, method=SecondFactorMethod.EMAIL, destination="other@example.com"
    )
    ticket, _, _ = send_factor_code(other, factor)
    client = Client()
    headers = _register(client)

    response = client.post(
        "/api/v1/auth/2fa/email/confirm",
        {"ticket": ticket, "code": _sent_code()},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 400


def test_a_wrong_sent_code_is_rejected_without_ending_the_sign_in(db: None) -> None:
    client = Client()
    headers = _register(client)
    _enroll_email(client, headers)
    ticket = _sign_in(client)["login_ticket"]
    client.post(
        CHALLENGE, {"login_ticket": ticket, "method": "email"}, content_type="application/json"
    )

    wrong = client.post(
        VERIFY, {"login_ticket": ticket, "code": "000000"}, content_type="application/json"
    )
    assert wrong.status_code == 400

    right = client.post(
        VERIFY,
        {"login_ticket": ticket, "code": _sent_code()},
        content_type="application/json",
    )
    assert right.status_code == 200


def test_a_lone_sent_code_factor_still_needs_its_code_requested(db: None) -> None:
    """Only one factor is enrolled, so it is inferred -- but nothing was sent yet."""
    client = Client()
    headers = _register(client)
    _enroll_email(client, headers)
    ticket = _sign_in(client)["login_ticket"]

    response = client.post(
        VERIFY, {"login_ticket": ticket, "code": "000000"}, content_type="application/json"
    )

    assert response.status_code == 400
    assert "Request a code before verifying it." in response.json()["description"]


def test_confirming_sms_after_the_factor_was_removed_is_refused(db: None) -> None:
    """The code is still valid, but there is no longer an enrolment to confirm."""
    client = Client()
    headers = _register(client)
    enrolled = client.post(
        "/api/v1/auth/2fa/sms/enroll",
        {"phone": PHONE},
        content_type="application/json",
        **headers,
    )
    code = _sent_code()
    SecondFactor.objects.filter(method=SecondFactorMethod.SMS).delete()

    response = client.post(
        "/api/v1/auth/2fa/sms/confirm",
        {"ticket": enrolled.json()["data"]["ticket"], "code": code},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 400
    assert "Start SMS enrolment first." in response.json()["description"]


def test_confirming_email_after_the_factor_was_removed_is_refused(db: None) -> None:
    client = Client()
    headers = _register(client)
    enrolled = client.post("/api/v1/auth/2fa/email/enroll", **headers)
    code = _sent_code()
    SecondFactor.objects.filter(method=SecondFactorMethod.EMAIL).delete()

    response = client.post(
        "/api/v1/auth/2fa/email/confirm",
        {"ticket": enrolled.json()["data"]["ticket"], "code": code},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 400
    assert "Start email enrolment first." in response.json()["description"]


def test_confirming_sms_leaves_an_already_verified_number_alone(db: None) -> None:
    """The number was proved during an SMS sign-in, so enrolment adds nothing to it."""
    client = Client()
    headers = _register(client)
    user = get_user_model()._default_manager.get(username="zoe")
    PhoneNumber.objects.create(user=user, number=PHONE, is_verified=True)
    verified_at = PhoneNumber.objects.get(number=PHONE).verified_at

    enrolled = client.post(
        "/api/v1/auth/2fa/sms/enroll",
        {"phone": PHONE},
        content_type="application/json",
        **headers,
    )
    response = client.post(
        "/api/v1/auth/2fa/sms/confirm",
        {"ticket": enrolled.json()["data"]["ticket"], "code": _sent_code()},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    record = PhoneNumber.objects.get(number=PHONE)
    assert record.is_verified is True
    assert record.verified_at == verified_at
