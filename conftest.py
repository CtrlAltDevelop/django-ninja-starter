"""Test-wide isolation for the process-global authentication helpers."""

from collections.abc import Iterator

import pytest

from infrastructure.auth.core import delivery
from infrastructure.auth.core.challenges import reset_challenge_store


@pytest.fixture(autouse=True)
def _isolate_auth_state() -> Iterator[None]:
    """Give every test an empty challenge store and an empty outbox.

    The in-memory store and the capturing delivery backends both live for the
    life of the process, so without this a ticket or a code from one test would
    still be sitting there during the next.
    """
    reset_challenge_store()
    delivery.outbox.clear()
    yield
    reset_challenge_store()
    delivery.outbox.clear()
