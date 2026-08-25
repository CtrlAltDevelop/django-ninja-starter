"""The challenge-store contract, proved against both implementations.

Tests run on the in-memory store but deployments run on Redis, so every case
here is parametrised across both. A behaviour that held only in memory would
otherwise be a bug that no test could see.
"""

from collections.abc import Iterator

import fakeredis
import pytest
from django.test import override_settings

from infrastructure.auth.core.challenges import (
    ChallengeAttemptsExhausted,
    ChallengeExpired,
    ChallengeStore,
    InvalidCode,
    LocMemChallengeStore,
    RedisChallengeStore,
)
from infrastructure.auth.core.codes import codes_match, generate_numeric_code, hash_code


@pytest.fixture(params=["locmem", "redis"])
def store(request: pytest.FixtureRequest) -> Iterator[ChallengeStore]:
    if request.param == "locmem":
        yield LocMemChallengeStore()
        return
    client = fakeredis.FakeRedis()
    yield RedisChallengeStore(client=client)
    client.flushall()


def test_code_survives_a_round_trip(store: ChallengeStore) -> None:
    ticket = store.create(purpose="login", subject="7", code="123456", destination="a@b.test")

    challenge = store.verify(ticket, "123456", purpose="login")

    assert challenge.subject == "7"
    assert challenge.destination == "a@b.test"


def test_a_verified_code_cannot_be_replayed(store: ChallengeStore) -> None:
    ticket = store.create(purpose="login", subject="7", code="123456")
    store.verify(ticket, "123456", purpose="login")

    with pytest.raises(ChallengeExpired):
        store.verify(ticket, "123456", purpose="login")


def test_wrong_code_leaves_the_challenge_usable(store: ChallengeStore) -> None:
    ticket = store.create(purpose="login", subject="7", code="123456")

    with pytest.raises(InvalidCode):
        store.verify(ticket, "000000", purpose="login")

    assert store.verify(ticket, "123456", purpose="login").subject == "7"


@override_settings(AUTH_CHALLENGE_MAX_ATTEMPTS=2)
def test_guessing_stops_after_the_attempt_cap(store: ChallengeStore) -> None:
    ticket = store.create(purpose="login", subject="7", code="123456")
    for _ in range(2):
        with pytest.raises(InvalidCode):
            store.verify(ticket, "000000", purpose="login")

    with pytest.raises(ChallengeAttemptsExhausted):
        store.verify(ticket, "123456", purpose="login")


def test_a_ticket_is_bound_to_the_purpose_that_minted_it(store: ChallengeStore) -> None:
    ticket = store.create(purpose="login", subject="7", code="123456")

    with pytest.raises(InvalidCode):
        store.verify(ticket, "123456", purpose="signup")


def test_presenting_the_wrong_purpose_destroys_the_ticket(store: ChallengeStore) -> None:
    """A mismatched purpose is an attack signal, not a typo, so the ticket dies."""
    ticket = store.create(purpose="login", subject="7", code="123456")
    with pytest.raises(InvalidCode):
        store.verify(ticket, "123456", purpose="signup")

    with pytest.raises(ChallengeExpired):
        store.verify(ticket, "123456", purpose="login")


def test_codeless_tickets_cannot_be_verified_with_an_empty_code(store: ChallengeStore) -> None:
    ticket = store.create(purpose="link", subject="7")

    with pytest.raises(InvalidCode):
        store.verify(ticket, "", purpose="link")


def test_reading_a_codeless_ticket_leaves_it_in_place(store: ChallengeStore) -> None:
    ticket = store.create(purpose="pending", subject="7", metadata={"methods": ["totp"]})

    assert store.read(ticket, purpose="pending").metadata == {"methods": ["totp"]}
    assert store.read(ticket, purpose="pending").subject == "7"


def test_reading_an_unknown_ticket_reports_expiry(store: ChallengeStore) -> None:
    with pytest.raises(ChallengeExpired):
        store.read("never-issued", purpose="pending")


@override_settings(AUTH_CHALLENGE_MAX_ATTEMPTS=2)
def test_repeated_failures_retire_a_pending_ticket(store: ChallengeStore) -> None:
    ticket = store.create(purpose="pending", subject="7")

    assert store.fail(ticket) == 1
    assert store.fail(ticket) == 2
    assert store.fail(ticket) == 3

    with pytest.raises(ChallengeExpired):
        store.read(ticket, purpose="pending")


def test_failing_an_unknown_ticket_counts_nothing(store: ChallengeStore) -> None:
    assert store.fail("never-issued") == 0


def test_metadata_can_be_replaced_without_a_new_ticket(store: ChallengeStore) -> None:
    ticket = store.create(purpose="pending", subject="7", metadata={"methods": ["sms"]})

    store.update_metadata(ticket, purpose="pending", metadata={"methods": ["sms"], "code": "x"})

    assert store.read(ticket, purpose="pending").metadata["code"] == "x"


def test_expired_challenges_report_themselves_as_expired(store: ChallengeStore) -> None:
    ticket = store.create(purpose="login", subject="7", code="123456", ttl=-1)

    with pytest.raises(ChallengeExpired):
        store.verify(ticket, "123456", purpose="login")


def test_counters_climb_within_their_window(store: ChallengeStore) -> None:
    assert store.increment("k", 60) == 1
    assert store.increment("k", 60) == 2


def test_discarding_a_ticket_makes_it_unusable(store: ChallengeStore) -> None:
    ticket = store.create(purpose="login", subject="7", code="123456")
    store.discard(ticket)

    with pytest.raises(ChallengeExpired):
        store.verify(ticket, "123456", purpose="login")


def test_two_tickets_never_share_a_slot(store: ChallengeStore) -> None:
    first = store.create(purpose="login", subject="1", code="111111")
    second = store.create(purpose="login", subject="2", code="222222")

    assert first != second
    assert store.verify(second, "222222", purpose="login").subject == "2"
    assert store.verify(first, "111111", purpose="login").subject == "1"


@override_settings(AUTH_CODE_DIGITS=6)
def test_generated_codes_keep_their_leading_zeros() -> None:
    assert all(len(generate_numeric_code()) == 6 for _ in range(50))


def test_short_codes_are_refused() -> None:
    with pytest.raises(ValueError, match="at least 4 digits"):
        generate_numeric_code(3)


def test_a_code_hash_is_bound_to_its_ticket_and_purpose() -> None:
    assert hash_code("t1", "login", "123456") != hash_code("t2", "login", "123456")
    assert hash_code("t1", "login", "123456") != hash_code("t1", "signup", "123456")


def test_empty_operands_never_match() -> None:
    assert codes_match("", "t", "login", "") is False
    assert codes_match(hash_code("t", "login", ""), "t", "login", "") is False
