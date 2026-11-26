"""Signing, the SSRF guard, and the retry schedule."""

import hashlib
import hmac
import urllib.error
from datetime import timedelta
from typing import Any
from unittest import mock

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.webhooks import services
from apps.webhooks.models import Delivery, DeliveryStatus
from apps.webhooks.tests.conftest import resolving_to
from infrastructure.common.errors import ApiError

URL = "https://hooks.example.test/in"


def test_the_signature_is_hmac_sha256_over_timestamp_and_body() -> None:
    expected = hmac.new(b"key", b"1700000000.{}", hashlib.sha256).hexdigest()

    assert services.sign("key", 1700000000, b"{}") == f"t=1700000000,v1={expected}"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "169.254.169.254",
        "::1",
        "fe80::1",
        "::ffff:10.0.0.1",
    ],
)
def test_private_loopback_and_link_local_targets_are_refused(address: str) -> None:
    with resolving_to(address), pytest.raises(services.UnsafeTarget):
        services.assert_public_target(URL)


def test_plain_http_is_refused() -> None:
    with pytest.raises(services.UnsafeTarget):
        services.assert_public_target("http://hooks.example.test/in")


def test_registration_refuses_an_unsafe_url(alice: Any) -> None:
    with resolving_to("127.0.0.1"), pytest.raises(ApiError):
        services.create_endpoint(alice, URL, ["order.paid"])


def test_registration_refuses_malformed_event_types(alice: Any) -> None:
    with pytest.raises(ApiError):
        services.create_endpoint(alice, URL, ["Not An Event"])


def _queued(user: Any) -> Delivery:
    services.create_endpoint(user, URL, ["order.paid"])
    (delivery,) = services.emit(user, "order.paid", {"order": 1})
    return delivery


def test_emit_queues_only_for_subscribed_endpoints(alice: Any, bob: Any) -> None:
    services.create_endpoint(alice, URL, ["order.paid"])
    services.create_endpoint(bob, URL, ["order.paid"])

    assert len(services.emit(alice, "order.paid", {})) == 1
    assert services.emit(alice, "order.refunded", {}) == []


def _response(status: int) -> Any:
    response = mock.MagicMock(status=status)
    response.__enter__.return_value = response
    return response


def test_a_delivery_is_signed_and_recorded(alice: Any) -> None:
    delivery = _queued(alice)
    with mock.patch.object(services._opener, "open", return_value=_response(204)) as sent:
        assert services.deliver_due() == 1

    request = sent.call_args.args[0]
    timestamp = request.get_header("X-webhook-signature").split(",")[0][2:]
    assert request.get_header("X-webhook-signature") == services.sign(
        delivery.endpoint.secret, int(timestamp), request.data
    )
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SUCCEEDED
    assert delivery.attempt_log.get().status_code == 204


def test_a_failure_backs_off_exponentially_then_gives_up(alice: Any, settings: Any) -> None:
    settings.WEBHOOKS_MAX_ATTEMPTS = 3
    settings.WEBHOOKS_BACKOFF_SECONDS = 10
    delivery = _queued(alice)
    failure = urllib.error.HTTPError(URL, 500, "boom", {}, None)  # type: ignore[arg-type]
    waits = []
    with mock.patch.object(services._opener, "open", side_effect=failure):
        for _ in range(3):
            before = timezone.now()
            services.attempt(Delivery.objects.select_related("endpoint").get(id=delivery.id))
            delivery.refresh_from_db()
            if delivery.next_attempt_at:
                waits.append(round((delivery.next_attempt_at - before).total_seconds()))

    assert waits == [10, 20]
    assert delivery.status == DeliveryStatus.FAILED
    assert [a.status_code for a in delivery.attempt_log.all()] == [500, 500, 500]


def test_the_ssrf_guard_runs_again_at_send_time(alice: Any) -> None:
    delivery = _queued(alice)
    with resolving_to("10.0.0.7"), mock.patch.object(services._opener, "open") as sent:
        services.deliver_due()

    sent.assert_not_called()
    delivery.refresh_from_db()
    assert "private" in delivery.attempt_log.get().error
    assert delivery.status == DeliveryStatus.PENDING


def test_deliveries_not_yet_due_wait(alice: Any) -> None:
    delivery = _queued(alice)
    Delivery.objects.filter(id=delivery.id).update(
        next_attempt_at=timezone.now() + timedelta(minutes=5)
    )
    with mock.patch.object(services._opener, "open") as sent:
        call_command("webhooks_deliver")

    sent.assert_not_called()
