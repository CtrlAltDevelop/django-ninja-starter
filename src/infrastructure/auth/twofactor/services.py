"""Enrolling, challenging, and checking each kind of second factor.

The primitives below are the vocabulary: mint a secret, send a code, spend a
recovery code. :class:`TwoFactorService` at the foot of the file is the grammar
-- the login-time challenge and each enrolment, in the order they have to
happen. The three transport packages beside this file call the service; none of
them reaches past it to a primitive.
"""

import hmac
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import pyotp
from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from infrastructure.auth.core.challenges import Challenge, InvalidCode, get_challenge_store
from infrastructure.auth.core.codes import generate_numeric_code
from infrastructure.auth.core.delivery import send_email, send_sms
from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.flows import (
    PENDING_PURPOSE,
    pending_methods,
    record_event,
    resolve_pending_login,
)
from infrastructure.auth.core.identities import account_email, account_phone, normalize_phone
from infrastructure.auth.core.models import AuthEventType, PhoneNumber
from infrastructure.auth.core.services import masking_service
from infrastructure.auth.core.sessions import IssuedCredentials, issue_credentials
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


INFERABLE = (SecondFactorMethod.TOTP, SecondFactorMethod.SMS, SecondFactorMethod.EMAIL)


@dataclass(frozen=True, slots=True)
class SentCode:
    """Where a second-factor code went, and the handle for redeeming it."""

    ticket: str
    channel: str
    destination: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class EnrolledFactorView:
    """One factor on an account, as every transport reports it."""

    method: str
    destination: str
    confirmed: bool
    last_used_at: str


@dataclass(frozen=True, slots=True)
class FactorList:
    """What is enrolled, what this deployment allows, and what is left in reserve."""

    methods: list[EnrolledFactorView]
    available: list[str]
    unused_recovery_codes: int


@dataclass(frozen=True, slots=True)
class TotpEnrolment:
    """The shared secret, in the two forms an authenticator app accepts."""

    secret: str
    otpauth_uri: str


class TwoFactorService:
    """The login-time challenge, and everything an account does to its factors."""

    def chosen_method(self, metadata: dict[str, Any], methods: list[str], requested: str) -> str:
        """Work out which factor the caller means, without ever guessing recovery.

        Spending a recovery code on what was meant to be a mistyped authenticator
        code would burn a credential the user may have printed out months ago, so
        recovery is only ever used when asked for by name.
        """
        if requested:
            return requested
        if metadata.get("code_ticket") and metadata.get("code_method"):
            return str(metadata["code_method"])
        candidates = [method for method in methods if method in INFERABLE]
        if SecondFactorMethod.TOTP in candidates:
            return SecondFactorMethod.TOTP
        if len(candidates) == 1:
            return candidates[0]
        raise AuthError(
            "Specify which second factor you are using.",
            status=400,
            title=ResponseTitle.SECOND_FACTOR_UNSPECIFIED,
        )

    def challenge(self, request: HttpRequest, *, login_ticket: str, method: str) -> SentCode:
        """Deliver an SMS or email code for a login that is waiting on a second factor."""
        user, metadata = resolve_pending_login(login_ticket)
        if method not in pending_methods(metadata):
            raise AuthError(
                "That second factor is not set up for this account.",
                status=400,
                title=ResponseTitle.SECOND_FACTOR_NOT_SET_UP,
            )
        factor = factor_for(user, method)
        ticket, channel, destination = send_factor_code(user, factor)
        get_challenge_store().update_metadata(
            login_ticket,
            purpose=PENDING_PURPOSE,
            metadata={**metadata, "code_ticket": ticket, "code_method": method},
        )
        record_event(
            request,
            AuthEventType.CODE_SENT,
            user=user,
            method=method,
            identifier=destination,
            stage="second_factor",
        )
        return SentCode(
            ticket=login_ticket,
            channel=channel,
            destination=masking_service.destination(channel, destination),
            expires_in=settings.AUTH_CHALLENGE_TTL_SECONDS,
        )

    def verify(
        self, request: HttpRequest, *, login_ticket: str, code: str, method: str = ""
    ) -> IssuedCredentials:
        """Check the second factor and, if it holds, issue the credential."""
        store = get_challenge_store()
        user, metadata = resolve_pending_login(login_ticket)
        methods = pending_methods(metadata)
        chosen = self.chosen_method(metadata, methods, method)
        if chosen not in methods:
            raise AuthError(
                "That second factor is not set up for this account.",
                status=400,
                title=ResponseTitle.SECOND_FACTOR_NOT_SET_UP,
            )

        code_ticket = ""
        if chosen == SecondFactorMethod.TOTP:
            accepted = verify_totp(factor_for(user, chosen), code)
        elif chosen == SecondFactorMethod.RECOVERY:
            accepted = consume_recovery_code(user, code)
        else:
            code_ticket = str(metadata.get("code_ticket", ""))
            if not code_ticket or metadata.get("code_method") != chosen:
                raise AuthError(
                    "Request a code before verifying it.",
                    status=400,
                    title=ResponseTitle.CODE_NOT_REQUESTED,
                )
            try:
                redeem_factor_code(code_ticket, code, user)
            except InvalidCode:
                accepted = False
            else:
                accepted = True
                factor_for(user, chosen).mark_used()

        if not accepted:
            store.fail(login_ticket)
            record_event(request, AuthEventType.SECOND_FACTOR_FAILED, user=user, method=chosen)
            raise AuthError("That code is not valid.", status=400, title=ResponseTitle.INVALID_CODE)

        store.discard(login_ticket)
        if code_ticket:
            store.discard(code_ticket)
        first_factor = str(metadata.get("first_factor", ""))
        record_event(request, AuthEventType.SECOND_FACTOR_SUCCEEDED, user=user, method=chosen)
        credentials = issue_credentials(request, user, method=f"{first_factor}+{chosen}")
        record_event(
            request,
            AuthEventType.LOGIN_SUCCEEDED,
            user=user,
            method=first_factor,
            second_factor=chosen,
        )
        return credentials

    def factors(self, user: Any) -> FactorList:
        return FactorList(
            methods=[
                EnrolledFactorView(
                    method=factor.method,
                    destination=masking_service.destination(
                        "sms" if factor.method == SecondFactorMethod.SMS else "email",
                        factor.destination,
                    )
                    if factor.destination
                    else "",
                    confirmed=factor.is_confirmed,
                    last_used_at=factor.last_used_at.isoformat() if factor.last_used_at else "",
                )
                for factor in SecondFactor.objects.filter(user=user)
            ],
            available=list(settings.AUTH_SECOND_FACTORS),
            unused_recovery_codes=unused_recovery_codes(user),
        )

    def start_totp(self, user: Any) -> TotpEnrolment:
        """Hand back a fresh secret. It counts for nothing until a code confirms it."""
        _, secret, uri = begin_totp(user)
        return TotpEnrolment(secret=secret, otpauth_uri=uri)

    def confirm_totp(self, request: HttpRequest, user: Any, *, code: str) -> str:
        require_enabled(SecondFactorMethod.TOTP)
        factor = SecondFactor.objects.filter(user=user, method=SecondFactorMethod.TOTP).first()
        if factor is None:
            raise AuthError(
                "Start authenticator enrolment first.",
                status=400,
                title=ResponseTitle.ENROLMENT_NOT_STARTED,
            )
        if not verify_totp(factor, code):
            raise AuthError("That code is not valid.", status=400, title=ResponseTitle.INVALID_CODE)
        factor.confirm()
        self._record_enrolment(request, user, SecondFactorMethod.TOTP)
        return "Authenticator app enabled."

    def start_sms(self, user: Any, *, phone: str) -> SentCode:
        return self._sent(user, enroll_sms(user, phone))

    def confirm_sms(self, request: HttpRequest, user: Any, *, ticket: str, code: str) -> str:
        require_enabled(SecondFactorMethod.SMS)
        challenge = redeem_factor_code(ticket, code, user)
        factor = SecondFactor.objects.filter(user=user, method=SecondFactorMethod.SMS).first()
        if factor is None:
            raise AuthError(
                "Start SMS enrolment first.",
                status=400,
                title=ResponseTitle.ENROLMENT_NOT_STARTED,
            )
        factor.confirm()
        number = PhoneNumber.objects.filter(user=user, number=challenge.destination).first()
        if number is not None and not number.is_verified:
            number.mark_verified()
        self._record_enrolment(request, user, SecondFactorMethod.SMS)
        return "SMS codes enabled."

    def start_email(self, user: Any) -> SentCode:
        return self._sent(user, enroll_email(user))

    def confirm_email(self, request: HttpRequest, user: Any, *, ticket: str, code: str) -> str:
        require_enabled(SecondFactorMethod.EMAIL)
        redeem_factor_code(ticket, code, user)
        factor = SecondFactor.objects.filter(user=user, method=SecondFactorMethod.EMAIL).first()
        if factor is None:
            raise AuthError(
                "Start email enrolment first.",
                status=400,
                title=ResponseTitle.ENROLMENT_NOT_STARTED,
            )
        factor.confirm()
        self._record_enrolment(request, user, SecondFactorMethod.EMAIL)
        return "Email codes enabled."

    def generate_recovery_codes(self, request: HttpRequest, user: Any) -> list[str]:
        """Issue a new set. Any codes handed out earlier stop working immediately."""
        codes = issue_recovery_codes(user)
        self._record_enrolment(request, user, SecondFactorMethod.RECOVERY)
        return codes

    def remove(self, request: HttpRequest, user: Any, *, method: str) -> str:
        factor = SecondFactor.objects.filter(user=user, method=method).first()
        if factor is None:
            raise AuthError(
                "That second factor is not set up for this account.",
                status=404,
                title=ResponseTitle.SECOND_FACTOR_NOT_SET_UP,
            )
        factor.delete()
        if method == SecondFactorMethod.RECOVERY:
            user.auth_recovery_codes.all().delete()
        record_event(request, AuthEventType.SECOND_FACTOR_REMOVED, user=user, method=method)
        return f"{method} second factor removed."

    def _sent(self, user: Any, factor: SecondFactor) -> SentCode:
        ticket, channel, destination = send_factor_code(user, factor)
        return SentCode(
            ticket=ticket,
            channel=channel,
            destination=masking_service.destination(channel, destination),
            expires_in=settings.AUTH_CHALLENGE_TTL_SECONDS,
        )

    def _record_enrolment(self, request: HttpRequest, user: Any, method: str) -> None:
        record_event(request, AuthEventType.SECOND_FACTOR_ENROLLED, user=user, method=method)


twofactor_service = TwoFactorService()
