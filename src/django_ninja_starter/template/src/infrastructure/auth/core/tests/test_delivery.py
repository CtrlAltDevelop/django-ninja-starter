"""The swappable transports that carry codes and links."""

import logging
from unittest.mock import patch

import pytest
from django.core import mail
from django.test import override_settings

from infrastructure.auth.core import delivery
from infrastructure.auth.core.delivery import (
    ConsoleEmailBackend,
    ConsoleSmsBackend,
    DeliveryError,
    DjangoEmailBackend,
    send_email,
    send_sms,
)

DJANGO_EMAIL = "infrastructure.auth.core.delivery.DjangoEmailBackend"
CONSOLE_SMS = "infrastructure.auth.core.delivery.ConsoleSmsBackend"
CONSOLE_EMAIL = "infrastructure.auth.core.delivery.ConsoleEmailBackend"


def test_the_capturing_backends_record_what_was_sent() -> None:
    send_sms("+14155550101", "code 123456")
    send_email("zoe@example.com", "Subject", "code 123456")

    assert [(item.channel, item.destination) for item in delivery.outbox] == [
        ("sms", "+14155550101"),
        ("email", "zoe@example.com"),
    ]


@override_settings(AUTH_SMS_BACKEND=CONSOLE_SMS)
def test_the_console_sms_backend_logs_instead_of_sending(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="infrastructure.auth.core.delivery"):
        send_sms("+14155550101", "code 123456")

    assert "+14155550101" in caplog.text
    assert delivery.outbox == []


def test_the_console_email_backend_logs_instead_of_sending(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="infrastructure.auth.core.delivery"):
        ConsoleEmailBackend().send("zoe@example.com", "Subject", "body")

    assert "zoe@example.com" in caplog.text


def test_the_console_sms_backend_can_be_used_directly(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="infrastructure.auth.core.delivery"):
        ConsoleSmsBackend().send("+14155550101", "body")

    assert "body" in caplog.text


@override_settings(AUTH_EMAIL_BACKEND=DJANGO_EMAIL, AUTH_EMAIL_FROM="no-reply@example.test")
def test_the_django_backend_hands_the_message_to_django_mail() -> None:
    """Django's test runner already captures mail, so no backend override is needed."""
    send_email("zoe@example.com", "Your code", "code 123456")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["zoe@example.com"]
    assert mail.outbox[0].from_email == "no-reply@example.test"
    assert mail.outbox[0].subject == "Your code"


def test_a_transport_failure_surfaces_as_a_delivery_error() -> None:
    """Callers should see one error type, not whichever socket error leaked out."""
    with (
        patch(
            "infrastructure.auth.core.delivery.send_mail",
            side_effect=OSError("connection refused"),
        ),
        pytest.raises(DeliveryError, match="connection refused"),
    ):
        DjangoEmailBackend().send("zoe@example.com", "Subject", "body")
