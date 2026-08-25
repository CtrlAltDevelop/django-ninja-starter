"""Short-lived, hashed login challenges held outside the database.

A challenge is the server-side half of a two-step sign-in: step one mints a
ticket and delivers a code, step two trades the ticket plus the code for a
session. Neither the ticket nor the code is ever stored in readable form, and
the record disappears on its own when the TTL lapses.

Two shapes exist. A *coded* challenge carries a fingerprint of a short code and
is settled with :meth:`verify`. A *codeless* challenge carries no code at all --
it is the pending-login ticket handed out between a first and second factor, and
its own 256 bits of entropy are the whole credential. Codeless tickets are read
with :meth:`read`, which deliberately does not consume the record, so that a
mistyped authenticator code does not force the user back through step one.
"""

import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, cast

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

from infrastructure.auth.core.codes import codes_match, generate_ticket, hash_code, ticket_key

if TYPE_CHECKING:  # pragma: no cover
    import redis

KEY_PREFIX = "auth:challenge:"
COUNTER_PREFIX = "auth:counter:"


class ChallengeError(RuntimeError):
    """Raised when a challenge cannot be created, found, or satisfied."""


class ChallengeExpired(ChallengeError):
    pass


class ChallengeAttemptsExhausted(ChallengeError):
    pass


class InvalidCode(ChallengeError):
    pass


@dataclass(frozen=True)
class Challenge:
    """What step two recovers once the right code is presented."""

    purpose: str
    subject: str
    channel: str
    destination: str
    metadata: dict[str, Any] = field(default_factory=dict)


class ChallengeStore(Protocol):
    def create(
        self,
        *,
        purpose: str,
        subject: str,
        code: str = "",
        channel: str = "",
        destination: str = "",
        metadata: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> str: ...

    def verify(self, ticket: str, code: str, *, purpose: str) -> Challenge: ...

    def read(self, ticket: str, *, purpose: str) -> Challenge: ...

    def update_metadata(self, ticket: str, *, purpose: str, metadata: dict[str, Any]) -> None: ...

    def fail(self, ticket: str) -> int: ...

    def discard(self, ticket: str) -> None: ...

    def increment(self, key: str, ttl: int) -> int: ...


def _record(
    purpose: str,
    subject: str,
    channel: str,
    destination: str,
    metadata: dict[str, Any] | None,
    code_hash: str,
) -> dict[str, str]:
    return {
        "purpose": purpose,
        "subject": subject,
        "channel": channel,
        "destination": destination,
        "metadata": json.dumps(metadata or {}),
        "code_hash": code_hash,
        "attempts": "0",
    }


def _challenge_from(record: dict[str, str]) -> Challenge:
    try:
        metadata = json.loads(record.get("metadata", "{}"))
    except json.JSONDecodeError:
        metadata = {}
    return Challenge(
        purpose=record.get("purpose", ""),
        subject=record.get("subject", ""),
        channel=record.get("channel", ""),
        destination=record.get("destination", ""),
        metadata=metadata if isinstance(metadata, dict) else {},
    )


class RedisChallengeStore:
    """Keeps challenges in Redis, keyed by a digest of the ticket."""

    def __init__(self, client: "redis.Redis | None" = None) -> None:
        self._client = client

    @property
    def client(self) -> "redis.Redis":
        """Connect on first use, importing the driver only when this store is chosen.

        Deferring the import is what lets ``redis`` be an optional dependency: a
        deployment running a different store should not have to install a client
        it will never open.
        """
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(settings.AUTH_REDIS_URL)
        return self._client

    def _key(self, ticket: str) -> str:
        return f"{KEY_PREFIX}{ticket_key(ticket)}"

    def _load(self, ticket: str, purpose: str) -> dict[str, str]:
        key = self._key(ticket)
        raw = cast(dict[bytes, bytes], self.client.hgetall(key))
        if not raw:
            raise ChallengeExpired("This code has expired. Request a new one.")
        record = {
            k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
            for k, v in raw.items()
        }
        if record.get("purpose") != purpose:
            self.client.delete(key)
            raise InvalidCode("That code is not valid.")
        return record

    def create(
        self,
        *,
        purpose: str,
        subject: str,
        code: str = "",
        channel: str = "",
        destination: str = "",
        metadata: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> str:
        ticket = generate_ticket()
        lifetime = ttl if ttl is not None else settings.AUTH_CHALLENGE_TTL_SECONDS
        code_hash = hash_code(ticket, purpose, code) if code else ""
        record = _record(purpose, subject, channel, destination, metadata, code_hash)
        key = self._key(ticket)
        pipeline = self.client.pipeline()
        pipeline.hset(key, mapping=record)
        pipeline.expire(key, lifetime)
        pipeline.execute()
        return ticket

    def verify(self, ticket: str, code: str, *, purpose: str) -> Challenge:
        record = self._load(ticket, purpose)
        key = self._key(ticket)
        attempts = cast(int, self.client.hincrby(key, "attempts", 1))
        if attempts > settings.AUTH_CHALLENGE_MAX_ATTEMPTS:
            self.client.delete(key)
            raise ChallengeAttemptsExhausted("Too many incorrect codes. Request a new one.")
        if not codes_match(record.get("code_hash", ""), ticket, purpose, code):
            raise InvalidCode("That code is not valid.")
        self.client.delete(key)
        return _challenge_from(record)

    def read(self, ticket: str, *, purpose: str) -> Challenge:
        return _challenge_from(self._load(ticket, purpose))

    def update_metadata(self, ticket: str, *, purpose: str, metadata: dict[str, Any]) -> None:
        self._load(ticket, purpose)
        self.client.hset(self._key(ticket), "metadata", json.dumps(metadata))

    def fail(self, ticket: str) -> int:
        key = self._key(ticket)
        if not self.client.exists(key):
            return 0
        attempts = cast(int, self.client.hincrby(key, "attempts", 1))
        if attempts > settings.AUTH_CHALLENGE_MAX_ATTEMPTS:
            self.client.delete(key)
        return attempts

    def discard(self, ticket: str) -> None:
        self.client.delete(self._key(ticket))

    def increment(self, key: str, ttl: int) -> int:
        full_key = f"{COUNTER_PREFIX}{key}"
        pipeline = self.client.pipeline()
        pipeline.incr(full_key)
        pipeline.expire(full_key, ttl, nx=True)
        current, _ = pipeline.execute()
        return int(current)


class LocMemChallengeStore:
    """In-process fallback for development and tests without a Redis server.

    Deliberately not safe across processes: a multi-worker deployment would hand
    step two to a worker that never saw step one.
    """

    def __init__(self) -> None:
        self._records: dict[str, tuple[float, dict[str, str]]] = {}
        self._counters: dict[str, tuple[float, int]] = {}

    def _live(self, key: str) -> dict[str, str] | None:
        entry = self._records.get(key)
        if entry is None:
            return None
        expires_at, record = entry
        if expires_at <= time.monotonic():
            self._records.pop(key, None)
            return None
        return record

    def _load(self, ticket: str, purpose: str) -> dict[str, str]:
        key = ticket_key(ticket)
        record = self._live(key)
        if record is None:
            raise ChallengeExpired("This code has expired. Request a new one.")
        if record.get("purpose") != purpose:
            self._records.pop(key, None)
            raise InvalidCode("That code is not valid.")
        return record

    def create(
        self,
        *,
        purpose: str,
        subject: str,
        code: str = "",
        channel: str = "",
        destination: str = "",
        metadata: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> str:
        ticket = generate_ticket()
        lifetime = ttl if ttl is not None else settings.AUTH_CHALLENGE_TTL_SECONDS
        code_hash = hash_code(ticket, purpose, code) if code else ""
        record = _record(purpose, subject, channel, destination, metadata, code_hash)
        self._records[ticket_key(ticket)] = (time.monotonic() + lifetime, record)
        return ticket

    def verify(self, ticket: str, code: str, *, purpose: str) -> Challenge:
        record = self._load(ticket, purpose)
        key = ticket_key(ticket)
        attempts = int(record.get("attempts", "0")) + 1
        record["attempts"] = str(attempts)
        if attempts > settings.AUTH_CHALLENGE_MAX_ATTEMPTS:
            self._records.pop(key, None)
            raise ChallengeAttemptsExhausted("Too many incorrect codes. Request a new one.")
        if not codes_match(record.get("code_hash", ""), ticket, purpose, code):
            raise InvalidCode("That code is not valid.")
        self._records.pop(key, None)
        return _challenge_from(record)

    def read(self, ticket: str, *, purpose: str) -> Challenge:
        return _challenge_from(self._load(ticket, purpose))

    def update_metadata(self, ticket: str, *, purpose: str, metadata: dict[str, Any]) -> None:
        self._load(ticket, purpose)["metadata"] = json.dumps(metadata)

    def fail(self, ticket: str) -> int:
        key = ticket_key(ticket)
        record = self._live(key)
        if record is None:
            return 0
        attempts = int(record.get("attempts", "0")) + 1
        record["attempts"] = str(attempts)
        if attempts > settings.AUTH_CHALLENGE_MAX_ATTEMPTS:
            self._records.pop(key, None)
        return attempts

    def discard(self, ticket: str) -> None:
        self._records.pop(ticket_key(ticket), None)

    def increment(self, key: str, ttl: int) -> int:
        entry = self._counters.get(key)
        now = time.monotonic()
        if entry is None or entry[0] <= now:
            self._counters[key] = (now + ttl, 1)
            return 1
        expires_at, count = entry
        self._counters[key] = (expires_at, count + 1)
        return count + 1


_store: ChallengeStore | None = None


def get_challenge_store() -> ChallengeStore:
    """Return the configured store, built once per process."""
    global _store
    if _store is None:
        try:
            _store = import_string(settings.AUTH_CHALLENGE_STORE)()
        except ImportError as error:
            raise ImproperlyConfigured(
                f"DJANGO_AUTH_CHALLENGE_STORE is not importable: {settings.AUTH_CHALLENGE_STORE}"
            ) from error
    return _store


def reset_challenge_store() -> None:
    global _store
    _store = None


@receiver(setting_changed)
def _reset_on_setting_change(setting: str, **kwargs: object) -> None:
    if setting in {"AUTH_CHALLENGE_STORE", "AUTH_REDIS_URL"}:
        reset_challenge_store()
