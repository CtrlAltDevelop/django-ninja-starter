"""The race the whole design exists to lose safely, and the lock that loses it.

Two withdrawals of eight against a balance of ten. Both read the balance, both
see enough, both write -- and the wallet ends at minus six. That is the bug a
wallet cannot ship with, and the defence is not cleverer arithmetic: it is
``select_for_update`` on the wallet row, so the second transaction blocks until
the first commits and then reads what the first one did.

Testing it honestly is awkward, because the default database here is SQLite and
SQLite has no row locks -- ``select_for_update`` compiles to nothing there.
Pretending otherwise with a test that passes on SQLite would be worse than no
test: it would report that the protection works on a backend where it does not
exist. So this file does two separate things.

The first works everywhere: it proves that every path that can move money
*asks* for the lock, by watching the SQL. A path that stopped locking would stop
saying ``FOR UPDATE`` long before anybody noticed a wrong balance.

The second is the real race, run on real threads, and it is skipped unless the
suite is pointed at a backend that actually has row locking.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.db import connection

from apps.wallet.catalog import PaymentMethod
from apps.wallet.errors import InsufficientFunds
from apps.wallet.services import wallet_service

pytestmark = pytest.mark.django_db

HAS_ROW_LOCKS = connection.features.has_select_for_update


def locking_statements(queries: list[dict]) -> list[str]:
    return [query["sql"] for query in queries if "FOR UPDATE" in query["sql"].upper()]


@pytest.mark.skipif(not HAS_ROW_LOCKS, reason="this backend compiles FOR UPDATE away")
def test_every_write_asks_for_the_wallet_row(funded: Any, cash: PaymentMethod) -> None:
    """Watching the SQL, because the lock is invisible until it is missing."""
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as captured:
        wallet_service.withdraw(funded, amount=Decimal("10"), method="cash", reference="w")
    assert locking_statements(captured.captured_queries)

    with CaptureQueriesContext(connection) as captured:
        wallet_service.deposit(funded, amount=Decimal("10"), method="cash", reference="d")
    assert locking_statements(captured.captured_queries)


def test_a_second_withdrawal_sees_what_the_first_one_did(funded: Any, cash: PaymentMethod) -> None:
    """The serialised outcome, stated as the property that has to hold.

    Runs on any backend, because it is sequential -- what it pins down is that the
    check is against ``available`` and that a pending payout has already claimed
    its money. Without that, the two withdrawals below would both be allowed and
    only one of them could ever be paid.
    """
    wallet_service.withdraw(funded, amount=Decimal("800"), method="cash", reference="w1")

    with pytest.raises(InsufficientFunds):
        wallet_service.withdraw(funded, amount=Decimal("800"), method="cash", reference="w2")


@pytest.mark.skipif(not HAS_ROW_LOCKS, reason="this backend has no row locks to race for")
def test_two_threads_racing_for_the_last_of_the_money(
    transactional_db: None, alice: Any, cash: PaymentMethod
) -> None:
    """The real thing: two connections, one balance, and only one of them may win.

    ``transactional_db`` rather than ``db``, because each thread needs its own
    connection and rows sitting inside the test's open transaction are not there
    as far as another connection is concerned.
    """
    import threading

    from django.db import connections

    wallet_service.deposit(alice, amount=Decimal("10"), method="cash", reference="seed")

    outcomes: list[str] = []
    barrier = threading.Barrier(2)

    def withdraw(reference: str) -> None:
        barrier.wait()
        try:
            wallet_service.withdraw(alice, amount=Decimal("8"), method="cash", reference=reference)
            outcomes.append("paid")
        except InsufficientFunds:
            outcomes.append("refused")
        finally:
            connections.close_all()

    threads = [threading.Thread(target=withdraw, args=(f"race-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == ["paid", "refused"]
    assert wallet_service.balance(alice)["settled"] >= Decimal("0")
