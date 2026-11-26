"""Every webhook decision, shared by REST, GraphQL, gRPC and the delivery command.

Code with something to say calls :func:`emit`; that only writes rows. The
sending happens in ``manage.py webhooks_deliver``, which a scheduler runs, the
same way ``support_prune`` and ``notifications_prune`` are run.
"""

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.webhooks.models import Delivery, DeliveryAttempt, DeliveryStatus, Endpoint
from infrastructure.common.errors import ApiError
from infrastructure.common.responses import ResponseTitle

SIGNATURE_HEADER = "X-Webhook-Signature"
EVENT_HEADER = "X-Webhook-Event"
EVENT_PATTERN = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
MAX_EVENTS = 50
MAX_ERROR_LENGTH = 2000


class UnsafeTarget(ValueError):
    """The URL resolves somewhere this server must never be made to call."""


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def assert_public_target(url: str) -> None:
    """Refuse anything but https to a host whose every address is public.

    Checked at registration and again right before every send, because DNS can
    change between the two.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise UnsafeTarget("Webhook URLs must be https.")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as error:
        raise UnsafeTarget(f"Cannot resolve {parts.hostname}.") from error
    addresses = {str(info[4][0]) for info in infos}
    if not addresses or not all(_is_public(address) for address in addresses):
        raise UnsafeTarget(f"{parts.hostname} resolves to a private or reserved address.")


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """HMAC-SHA256 over ``"{timestamp}." + body``, as the header value carries it."""
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={digest.hexdigest()}"


def _not_found() -> ApiError:
    return ApiError("No such webhook endpoint.", status=404, title=ResponseTitle.NOT_FOUND)


def _clean_events(events: list[str]) -> list[str]:
    cleaned = sorted({event.strip() for event in events})
    if not cleaned or len(cleaned) > MAX_EVENTS:
        raise ApiError(f"Subscribe to between 1 and {MAX_EVENTS} event types.")
    bad = [event for event in cleaned if not EVENT_PATTERN.fullmatch(event)]
    if bad:
        raise ApiError(f"Invalid event types: {', '.join(bad)}.")
    return cleaned


def endpoint_data(endpoint: Endpoint, *, with_secret: bool = False) -> dict[str, Any]:
    return {
        "id": str(endpoint.id),
        "url": endpoint.url,
        "events": list(endpoint.events),
        "is_active": endpoint.is_active,
        "created_at": endpoint.created_at.isoformat(),
        "secret": endpoint.secret if with_secret else "",
    }


def delivery_data(delivery: Delivery) -> dict[str, Any]:
    return {
        "id": str(delivery.id),
        "event": delivery.event,
        "status": str(delivery.status),
        "attempts": delivery.attempts,
        "next_attempt_at": (
            delivery.next_attempt_at.isoformat() if delivery.next_attempt_at else ""
        ),
        "created_at": delivery.created_at.isoformat(),
        "attempt_log": [
            {
                "number": attempt.number,
                "status_code": attempt.status_code or 0,
                "error": attempt.error,
                "duration_ms": attempt.duration_ms,
                "created_at": attempt.created_at.isoformat(),
            }
            for attempt in delivery.attempt_log.all()
        ],
    }


def _owned(user: Any, endpoint_id: str | UUID) -> Endpoint:
    try:
        return Endpoint.objects.get(id=UUID(str(endpoint_id)), owner=user)
    except (Endpoint.DoesNotExist, ValueError):
        raise _not_found() from None


def list_endpoints(user: Any) -> list[dict[str, Any]]:
    return [endpoint_data(endpoint) for endpoint in Endpoint.objects.filter(owner=user)]


def create_endpoint(user: Any, url: str, events: list[str]) -> dict[str, Any]:
    """Register an endpoint; the secret is shown in this answer and on rotation only."""
    try:
        assert_public_target(url)
    except UnsafeTarget as error:
        raise ApiError(str(error)) from None
    endpoint = Endpoint.objects.create(
        owner=user, url=url, events=_clean_events(events), secret=secrets.token_hex(32)
    )
    return endpoint_data(endpoint, with_secret=True)


def delete_endpoint(user: Any, endpoint_id: str | UUID) -> None:
    _owned(user, endpoint_id).delete()


def rotate_secret(user: Any, endpoint_id: str | UUID) -> dict[str, Any]:
    endpoint = _owned(user, endpoint_id)
    endpoint.secret = secrets.token_hex(32)
    endpoint.save(update_fields=["secret"])
    return endpoint_data(endpoint, with_secret=True)


def list_deliveries(user: Any, endpoint_id: str | UUID, limit: int = 50) -> list[dict[str, Any]]:
    endpoint = _owned(user, endpoint_id)
    rows = endpoint.deliveries.prefetch_related("attempt_log")[: max(1, min(limit, 200))]
    return [delivery_data(delivery) for delivery in rows]


def emit(user: Any, event: str, payload: dict[str, Any]) -> list[Delivery]:
    """Queue ``event`` for every active endpoint of ``user`` subscribed to it."""
    now = timezone.now()
    endpoints = Endpoint.objects.filter(owner=user, is_active=True)
    return [
        Delivery.objects.create(
            endpoint=endpoint, event=event, payload=payload, next_attempt_at=now
        )
        for endpoint in endpoints
        if event in endpoint.events
    ]


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect would be a second, unchecked target, so it is a failure instead."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


_opener = urllib.request.build_opener(_NoRedirects)


def _send(endpoint: Endpoint, delivery: Delivery) -> tuple[int | None, str]:
    """POST once. Returns the status code (None if none came back) and an error."""
    try:
        assert_public_target(endpoint.url)
    except UnsafeTarget as error:
        return None, str(error)
    # ponytail: the address is re-resolved by urllib after the check, so a
    # rebinding DNS server could still win the race; pin the IP if that matters.
    body = json.dumps(
        {"id": str(delivery.id), "event": delivery.event, "data": delivery.payload},
        default=str,
    ).encode()
    timestamp = int(time.time())
    request = urllib.request.Request(
        endpoint.url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "django-ninja-starter-webhooks",
            EVENT_HEADER: delivery.event,
            SIGNATURE_HEADER: sign(endpoint.secret, timestamp, body),
        },
    )
    try:
        with _opener.open(request, timeout=settings.WEBHOOKS_TIMEOUT_SECONDS) as response:
            return response.status, ""
    except urllib.error.HTTPError as error:
        return error.code, f"HTTP {error.code}"
    except (urllib.error.URLError, OSError) as error:
        return None, str(error)


def backoff(attempts: int) -> timedelta:
    """Wait after the ``attempts``-th failure: base, 2x base, 4x base, ..."""
    return timedelta(seconds=settings.WEBHOOKS_BACKOFF_SECONDS * 2 ** (attempts - 1))


def attempt(delivery: Delivery) -> Delivery:
    """Try one delivery once, record the try, and schedule the next or give up."""
    started = time.monotonic()
    status_code, error = _send(delivery.endpoint, delivery)
    delivery.attempts += 1
    DeliveryAttempt.objects.create(
        delivery=delivery,
        number=delivery.attempts,
        status_code=status_code,
        error=error[:MAX_ERROR_LENGTH],
        duration_ms=int((time.monotonic() - started) * 1000),
    )
    if status_code is not None and 200 <= status_code < 300:
        delivery.status, delivery.next_attempt_at = DeliveryStatus.SUCCEEDED, None
    elif delivery.attempts >= settings.WEBHOOKS_MAX_ATTEMPTS:
        delivery.status, delivery.next_attempt_at = DeliveryStatus.FAILED, None
    else:
        delivery.next_attempt_at = timezone.now() + backoff(delivery.attempts)
    delivery.save(update_fields=["status", "attempts", "next_attempt_at"])
    return delivery


def deliver_due(now: datetime | None = None, limit: int = 100) -> int:
    """Attempt every pending delivery whose time has come. Returns how many."""
    due = Delivery.objects.filter(
        status=DeliveryStatus.PENDING, next_attempt_at__lte=now or timezone.now()
    ).order_by("next_attempt_at")
    done = 0
    for delivery_id in list(due.values_list("id", flat=True)[:limit]):
        with transaction.atomic():
            delivery = (
                Delivery.objects.select_for_update()
                .select_related("endpoint")
                .filter(id=delivery_id, status=DeliveryStatus.PENDING)
                .first()
            )
            if delivery is not None:
                attempt(delivery)
                done += 1
    return done
