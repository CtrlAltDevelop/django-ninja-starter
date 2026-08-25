"""Pluggable transports for the codes and links a login flow sends out.

Every backend is swapped by dotted path, so a project bolts on Twilio, SNS, or a
transactional email vendor without touching the flows that call it. The defaults
never reach the network: SMS goes to the log, email goes through whatever Django
is already configured to use.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from django.core.mail import send_mail
from django.utils.module_loading import import_string

logger = logging.getLogger("infrastructure.auth.core.delivery")


class DeliveryError(RuntimeError):
    """Raised when a backend cannot hand the message to its carrier."""


@dataclass(frozen=True)
class SentMessage:
    """A delivery captured in memory rather than sent, for tests to assert on."""

    channel: str
    destination: str
    subject: str
    body: str


class SmsBackend(Protocol):
    def send(self, destination: str, body: str) -> None: ...


class EmailBackend(Protocol):
    def send(self, destination: str, subject: str, body: str) -> None: ...


class ConsoleSmsBackend:
    """Write the message to the log instead of paying a carrier in development."""

    def send(self, destination: str, body: str) -> None:
        logger.info("SMS to %s: %s", destination, body)


class ConsoleEmailBackend:
    def send(self, destination: str, subject: str, body: str) -> None:
        logger.info("Email to %s [%s]: %s", destination, subject, body)


class DjangoEmailBackend:
    """Send through ``django.core.mail``, honouring ``EMAIL_BACKEND``."""

    def send(self, destination: str, subject: str, body: str) -> None:
        try:
            send_mail(
                subject,
                body,
                settings.AUTH_EMAIL_FROM,
                [destination],
                fail_silently=False,
            )
        except OSError as error:
            raise DeliveryError(str(error)) from error


outbox: list[SentMessage] = []


class LocMemSmsBackend:
    """Collect messages in :data:`outbox` so tests can read the code back."""

    def send(self, destination: str, body: str) -> None:
        outbox.append(SentMessage("sms", destination, "", body))


class LocMemEmailBackend:
    def send(self, destination: str, subject: str, body: str) -> None:
        outbox.append(SentMessage("email", destination, subject, body))


def _backend(path: str) -> object:
    return import_string(path)()


def send_sms(destination: str, body: str) -> None:
    """Deliver a text message through the configured SMS backend."""
    backend: SmsBackend = _backend(settings.AUTH_SMS_BACKEND)  # type: ignore[assignment]
    backend.send(destination, body)


def send_email(destination: str, subject: str, body: str) -> None:
    """Deliver a mail message through the configured email backend."""
    backend: EmailBackend = _backend(settings.AUTH_EMAIL_BACKEND)  # type: ignore[assignment]
    backend.send(destination, subject, body)
