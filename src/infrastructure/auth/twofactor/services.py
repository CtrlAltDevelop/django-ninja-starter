"""Enrolling, challenging, and checking each kind of second factor."""

import hmac
import secrets
import time
from hashlib import sha256
from typing import Any

import pyotp
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from infrastructure.auth.core.challenges import Challenge, get_challenge_store
from infrastructure.auth.core.codes import generate_numeric_code
from infrastructure.auth.core.delivery import send_email, send_sms
from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.identities import account_email, account_phone, normalize_phone
from infrastructure.auth.core.models import PhoneNumber
from infrastructure.auth.core.throttling import guard_delivery
from infrastructure.auth.twofactor.models import RecoveryCode, SecondFactor, SecondFactorMethod
from infrastructure.common.responses import ResponseTitle

CODE_PURPOSE = "second_factor_code"
TOTP_PERIOD = 30
TOTP_WINDOW = 1
RECOVERY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
RECOVERY_GROUP = 5
RECOVERY_GROUPS = 2
DELIVERABLE = {SecondFactorMethod.SMS, SecondFactorMethod.EMAIL}


def require_enabled(method: str) -> None:
    """Reject a factor the deployment has not turned on."""
    if method not in settings.AUTH_SECOND_FACTORS:
        raise AuthError(
            f"The {method} second factor is not enabled.",
            status=404,
            title=ResponseTitle.SECOND_FACTOR_NOT_ENABLED,
        )


def confirmed_factors(user: Any) -> list[SecondFactor]:
    return list(SecondFactor.objects.filter(user=user, confirmed_at__isnull=False))


def factor_for(user: Any, method: str) -> SecondFactor:
    factor = SecondFactor.objects.filter(
        user=user, method=method, confirmed_at__isnull=False
    ).first()
    if factor is None:
        raise AuthError(
            f"No {method} second factor is set up for this account.",
            status=400,
            title=ResponseTitle.SECOND_FACTOR_NOT_SET_UP,
        )
    return factor


def begin_totp(user: Any) -> tuple[SecondFactor, str, str]:
    """Mint an unconfirmed authenticator secret and the URI a phone can scan."""
    require_enabled(SecondFactorMethod.TOTP)
    secret = pyotp.random_base32()
    factor, _ = SecondFactor.objects.get_or_create(
        user=user,
        method=SecondFactorMethod.TOTP,
        defaults={"last_counter": 0},
    )
    if factor.confirmed_at is not None:
        raise AuthError(
            "An authenticator app is already set up for this account.",
            status=409,
            title=ResponseTitle.SECOND_FACTOR_EXISTS,
        )
    factor.set_secret(secret)
    factor.last_counter = 0
    factor.save(update_fields=["secret_encrypted", "last_counter"])
    label = str(getattr(user, user.USERNAME_FIELD, user.pk))
    uri = pyotp.TOTP(secret, interval=TOTP_PERIOD).provisioning_uri(
        name=label,
        issuer_name=settings.AUTH_TOTP_ISSUER,
    )
    return factor, secret, uri


def verify_totp(factor: SecondFactor, code: str) -> bool:
    """Check a code against the enrolled secret, refusing to accept it twice.

    ``pyotp.verify`` alone would let the same code through for its whole window,
    so an accepted time step is recorded and anything at or before it is
    rejected.
    """
    secret = factor.secret()
    if not secret or not code.isdigit():
        return False
    totp = pyotp.TOTP(secret, interval=TOTP_PERIOD)
    current = int(time.time()) // TOTP_PERIOD
    for offset in range(-TOTP_WINDOW, TOTP_WINDOW + 1):
        counter = current + offset
        if not hmac.compare_digest(totp.at(counter * TOTP_PERIOD), code):
            continue
        if counter <= factor.last_counter:
            return False
        factor.mark_used(counter)
        return True
    return False


def _recovery_code() -> str:
    groups = [
        "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_GROUP))
        for _ in range(RECOVERY_GROUPS)
    ]
    return "-".join(groups)


def normalize_recovery_code(value: str) -> str:
    return value.strip().upper().replace("-", "").replace(" ", "")


def hash_recovery_code(value: str) -> str:
    return sha256(f"{settings.SECRET_KEY}:{normalize_recovery_code(value)}".encode()).hexdigest()


def issue_recovery_codes(user: Any) -> list[str]:
    """Replace any existing codes with a fresh set, returned in clear once."""
    require_enabled(SecondFactorMethod.RECOVERY)
    codes = [_recovery_code() for _ in range(settings.AUTH_RECOVERY_CODE_COUNT)]
    with transaction.atomic():
        RecoveryCode.objects.filter(user=user).delete()
        RecoveryCode.objects.bulk_create(
            RecoveryCode(user=user, code_hash=hash_recovery_code(code)) for code in codes
        )
        factor, _ = SecondFactor.objects.get_or_create(
            user=user, method=SecondFactorMethod.RECOVERY
        )
        if factor.confirmed_at is None:
            factor.confirm()
    return codes


def consume_recovery_code(user: Any, code: str) -> bool:
    """Spend one unused recovery code, if the presented value matches."""
    with transaction.atomic():
        record = (
            RecoveryCode.objects.select_for_update()
            .filter(user=user, code_hash=hash_recovery_code(code), used_at__isnull=True)
            .first()
        )
        if record is None:
            return False
        record.used_at = timezone.now()
        record.save(update_fields=["used_at"])
    return True


def unused_recovery_codes(user: Any) -> int:
    return RecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


def enroll_sms(user: Any, phone: str) -> SecondFactor:
    """Attach a number to the account as an unconfirmed SMS factor."""
    require_enabled(SecondFactorMethod.SMS)
    number = normalize_phone(phone)
    owner = PhoneNumber.objects.filter(number=number).select_related("user").first()
    if owner is not None and owner.user_id != user.pk:
        raise AuthError(
            "That phone number is already in use.", status=409, title=ResponseTitle.PHONE_IN_USE
        )
    if owner is None:
        PhoneNumber.objects.create(user=user, number=number, is_verified=False)
    factor, _ = SecondFactor.objects.get_or_create(user=user, method=SecondFactorMethod.SMS)
    factor.destination = number
    factor.save(update_fields=["destination"])
    return factor


def enroll_email(user: Any) -> SecondFactor:
    """Use the account's own address as an unconfirmed email factor."""
    require_enabled(SecondFactorMethod.EMAIL)
    address = account_email(user)
    if not address:
        raise AuthError(
            "This account has no email address.",
            status=400,
            title=ResponseTitle.ACCOUNT_HAS_NO_EMAIL,
        )
    factor, _ = SecondFactor.objects.get_or_create(user=user, method=SecondFactorMethod.EMAIL)
    factor.destination = address
    factor.save(update_fields=["destination"])
    return factor


def factor_destination(user: Any, factor: SecondFactor) -> str:
    """Return where this factor's code should go, falling back to the account."""
    if factor.destination:
        return factor.destination
    if factor.method == SecondFactorMethod.SMS:
        return account_phone(user)
    if factor.method == SecondFactorMethod.EMAIL:
        return account_email(user)
    return ""


def send_factor_code(user: Any, factor: SecondFactor) -> tuple[str, str, str]:
    """Deliver a one-time code for a deliverable factor.

    Returns the ticket that redeems it, the channel, and the raw destination.
    """
    if factor.method not in DELIVERABLE:
        raise AuthError(
            f"The {factor.method} factor does not use a sent code.",
            status=400,
            title=ResponseTitle.SECOND_FACTOR_NOT_CODE_BASED,
        )
    destination = factor_destination(user, factor)
    if not destination:
        raise AuthError(
            "This factor has no destination on file.",
            status=400,
            title=ResponseTitle.SECOND_FACTOR_NO_DESTINATION,
        )
    channel = "sms" if factor.method == SecondFactorMethod.SMS else "email"
    guard_delivery(f"2fa:{channel}", destination)
    code = generate_numeric_code()
    ticket = get_challenge_store().create(
        purpose=CODE_PURPOSE,
        subject=str(user.pk),
        code=code,
        channel=channel,
        destination=destination,
        metadata={"method": factor.method},
    )
    body = f"Your verification code is {code}."
    if channel == "sms":
        send_sms(destination, body)
    else:
        send_email(destination, "Your verification code", body)
    return ticket, channel, destination


def redeem_factor_code(ticket: str, code: str, user: Any) -> Challenge:
    """Settle a delivered code, refusing one minted for a different account."""
    challenge = get_challenge_store().verify(ticket, code, purpose=CODE_PURPOSE)
    if challenge.subject != str(user.pk):
        raise AuthError(
            "That code was issued for a different account.",
            status=400,
            title=ResponseTitle.INVALID_CODE,
        )
    return challenge
