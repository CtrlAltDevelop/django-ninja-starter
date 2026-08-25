"""Factor enrolment and checking, below the HTTP layer."""

import time
from typing import Any

import pyotp
import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.models import PhoneNumber
from infrastructure.auth.twofactor.models import SecondFactor, SecondFactorMethod
from infrastructure.auth.twofactor.services import (
    TOTP_PERIOD,
    begin_totp,
    confirmed_factors,
    consume_recovery_code,
    enroll_email,
    enroll_sms,
    factor_destination,
    factor_for,
    hash_recovery_code,
    issue_recovery_codes,
    normalize_recovery_code,
    redeem_factor_code,
    require_enabled,
    send_factor_code,
    unused_recovery_codes,
    verify_totp,
)

PHONE = "+14155550101"


@pytest.fixture
def user(db: None) -> Any:
    return get_user_model()._default_manager.create_user(username="zoe", email="zoe@example.com")


@override_settings(AUTH_SECOND_FACTORS=["totp"])
def test_a_factor_the_deployment_disabled_is_a_404() -> None:
    with pytest.raises(AuthError) as caught:
        require_enabled("sms")

    assert caught.value.status == 404


def test_asking_for_a_factor_the_account_lacks_is_an_error(user: Any) -> None:
    with pytest.raises(AuthError, match="No sms second factor"):
        factor_for(user, SecondFactorMethod.SMS)


def test_an_unconfirmed_factor_does_not_count_as_set_up(user: Any) -> None:
    SecondFactor.objects.create(user=user, method=SecondFactorMethod.TOTP)

    with pytest.raises(AuthError):
        factor_for(user, SecondFactorMethod.TOTP)
    assert confirmed_factors(user) == []


def test_a_totp_secret_survives_encryption(user: Any) -> None:
    factor, secret, uri = begin_totp(user)

    assert factor.secret() == secret
    assert uri.startswith("otpauth://totp/")
    assert "issuer=" in uri


def test_a_totp_code_is_accepted_once_then_refused(user: Any) -> None:
    _, secret, _ = begin_totp(user)
    factor = SecondFactor.objects.get(user=user)
    counter = int(time.time()) // TOTP_PERIOD
    code = pyotp.TOTP(secret, interval=TOTP_PERIOD).at(counter * TOTP_PERIOD)

    assert verify_totp(factor, code) is True
    assert verify_totp(factor, code) is False


def test_a_non_numeric_totp_code_is_refused(user: Any) -> None:
    begin_totp(user)
    factor = SecondFactor.objects.get(user=user)

    assert verify_totp(factor, "abcdef") is False


def test_a_factor_with_no_secret_accepts_nothing(user: Any) -> None:
    factor = SecondFactor.objects.create(user=user, method=SecondFactorMethod.TOTP)

    assert verify_totp(factor, "123456") is False


def test_recovery_codes_normalise_before_they_are_compared() -> None:
    assert normalize_recovery_code(" ab-cde ") == "ABCDE"
    assert hash_recovery_code("ab-cde") == hash_recovery_code("ABCDE")


def test_recovery_codes_are_issued_and_spent(user: Any) -> None:
    codes = issue_recovery_codes(user)

    assert unused_recovery_codes(user) == len(codes)
    assert consume_recovery_code(user, codes[0]) is True
    assert consume_recovery_code(user, codes[0]) is False
    assert unused_recovery_codes(user) == len(codes) - 1


def test_an_invented_recovery_code_is_refused(user: Any) -> None:
    issue_recovery_codes(user)

    assert consume_recovery_code(user, "ZZZZZ-ZZZZZ") is False


def test_enrolling_a_number_already_on_the_account_reuses_it(user: Any) -> None:
    PhoneNumber.objects.create(user=user, number=PHONE, is_verified=False)

    factor = enroll_sms(user, PHONE)

    assert factor.destination == PHONE
    assert PhoneNumber.objects.filter(number=PHONE).count() == 1


def test_enrolling_someone_elses_number_is_refused(user: Any) -> None:
    other = get_user_model()._default_manager.create_user(username="other")
    PhoneNumber.objects.create(user=other, number=PHONE, is_verified=True)

    with pytest.raises(AuthError) as caught:
        enroll_sms(user, PHONE)

    assert caught.value.status == 409


def test_email_enrolment_needs_an_address(db: None) -> None:
    account = get_user_model()._default_manager.create_user(username="noaddress")

    with pytest.raises(AuthError, match="no email address"):
        enroll_email(account)


def test_a_destination_falls_back_to_the_account(user: Any) -> None:
    """A factor enrolled before the snapshot existed still knows where to send."""
    PhoneNumber.objects.create(user=user, number=PHONE, is_verified=True)
    sms = SecondFactor.objects.create(user=user, method=SecondFactorMethod.SMS)
    email = SecondFactor.objects.create(user=user, method=SecondFactorMethod.EMAIL)

    assert factor_destination(user, sms) == PHONE
    assert factor_destination(user, email) == "zoe@example.com"


def test_a_factor_with_nowhere_to_send_is_an_error(db: None) -> None:
    account = get_user_model()._default_manager.create_user(username="noaddress")
    factor = SecondFactor.objects.create(user=account, method=SecondFactorMethod.SMS)

    with pytest.raises(AuthError, match="no destination"):
        send_factor_code(account, factor)


@pytest.mark.parametrize("method", [SecondFactorMethod.TOTP, SecondFactorMethod.RECOVERY])
def test_factors_without_a_sent_code_refuse_delivery(user: Any, method: str) -> None:
    factor = SecondFactor.objects.create(user=user, method=method)

    with pytest.raises(AuthError, match="does not use a sent code"):
        send_factor_code(user, factor)


def test_an_unknown_factor_destination_is_blank(user: Any) -> None:
    factor = SecondFactor.objects.create(user=user, method=SecondFactorMethod.TOTP)

    assert factor_destination(user, factor) == ""


def test_a_code_is_bound_to_the_account_it_was_minted_for(user: Any) -> None:
    from infrastructure.auth.core import delivery

    factor = enroll_email(user)
    ticket, _, _ = send_factor_code(user, factor)
    code = delivery.outbox[-1].body.rsplit(" ", 1)[1].rstrip(".")
    stranger = get_user_model()._default_manager.create_user(username="stranger")

    with pytest.raises(AuthError, match="different account"):
        redeem_factor_code(ticket, code, stranger)
