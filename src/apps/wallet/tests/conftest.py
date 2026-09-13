"""Fixtures for the wallet tests: a deployment somebody could actually pay into.

The app is optional, so its tests are too. A project that has not enabled it in
``DJANGO_WALLET_ENABLED`` has no wallet tables and no registered models, and
importing one raises before pytest can say anything useful -- so collection stops
here instead, and the rest of that project's suite runs as normal.

The catalogue below is deliberately not minimal. Most of what this app does only
shows up when there is more than one shape of method: one that settles at once
beside one that waits, one that charges a percentage beside one that charges a
percentage *and* a tax on that percentage, an asset that exists on two chains,
and a currency that is not the wallet's. Every one of those exists here once and
is shared by the tests that need it.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import RequestFactory

WALLET_INSTALLED = django_apps.is_installed("apps.wallet")
collect_ignore_glob = [] if WALLET_INSTALLED else ["*"]

if WALLET_INSTALLED:
    from apps.wallet.catalog import (
        Applies,
        Basis,
        ChargeKind,
        ExchangeRate,
        MethodCurrency,
        MethodFee,
        MethodNetwork,
        PaymentMethod,
    )
    from apps.wallet.methods import Method


def access_token(user: Any) -> str:
    """A real credential from the project's own issuer, not a hand-rolled JWT."""
    from infrastructure.auth.core.sessions import issue_credentials

    request = RequestFactory().post("/")
    return issue_credentials(request, user, method="password").access_token


def bearer(user: Any) -> dict[str, str]:
    """The header kwargs Django's test client wants for a signed-in request."""
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token(user)}"}


@pytest.fixture
def alice(db: None) -> Any:
    return get_user_model().objects.create_user(username="alice", email="alice@example.test")


@pytest.fixture
def bob(db: None) -> Any:
    return get_user_model().objects.create_user(username="bob", email="bob@example.test")


@pytest.fixture
def operator(db: None) -> Any:
    """Somebody who applies requests in the back office."""
    return get_user_model().objects.create_user(
        username="operator", email="ops@example.test", is_staff=True
    )


@pytest.fixture
def cash(db: None) -> "PaymentMethod":
    """Money over a counter: settles at once, and needs nobody's permission.

    The simplest method there is, and the one most tests use when what they are
    testing is not the method.
    """
    method = PaymentMethod.objects.create(
        code="cash",
        name="Cash",
        rail=str(Method.CASH),
        is_enabled=True,
        supports_deposit=True,
        supports_withdrawal=True,
        requires_approval=False,
    )
    MethodCurrency.objects.create(method=method, currency="USD", min_amount=Decimal("1"))
    return method


@pytest.fixture
def counter(db: None) -> "PaymentMethod":
    """Cash that an operator has to apply: the request flow, at its simplest."""
    method = PaymentMethod.objects.create(
        code="counter",
        name="Branch counter",
        rail=str(Method.CASH),
        is_enabled=True,
        supports_deposit=True,
        supports_withdrawal=True,
        requires_approval=True,
    )
    MethodCurrency.objects.create(method=method, currency="USD")
    return method


@pytest.fixture
def card(db: None) -> "PaymentMethod":
    """A card processor charging a commission with a tax on top of it.

    The interesting fee shape, and the reason :class:`Basis` exists: the tax is
    20% *of the commission*, not of the amount, which is how VAT on a payment fee
    actually works. A deployment that could only express the second would
    overcharge every customer it has.
    """
    method = PaymentMethod.objects.create(
        code="card",
        name="Card",
        rail=str(Method.CARD),
        is_enabled=True,
        supports_deposit=True,
        supports_withdrawal=True,
        requires_approval=False,
    )
    MethodCurrency.objects.create(
        method=method, currency="USD", min_amount=Decimal("5"), max_amount=Decimal("10000")
    )
    MethodCurrency.objects.create(method=method, currency="EUR", position=1)
    MethodFee.objects.create(
        method=method,
        kind=str(ChargeKind.COMMISSION),
        label="Processing",
        applies_to=str(Applies.BOTH),
        percent=Decimal("2.9"),
        fixed=Decimal("0.30"),
        position=0,
    )
    MethodFee.objects.create(
        method=method,
        kind=str(ChargeKind.TAX),
        label="VAT",
        applies_to=str(Applies.BOTH),
        percent=Decimal("20"),
        basis=str(Basis.CHARGES),
        position=1,
    )
    return method


@pytest.fixture
def crypto(db: None) -> "PaymentMethod":
    """USDT on two chains, with different fees and different address shapes.

    Two chains rather than one on purpose: a test that only ever sees a single
    network cannot catch the app defaulting to it, and defaulting is the mistake
    that pays somebody's money to an address on the wrong chain.
    """
    method = PaymentMethod.objects.create(
        code="usdt",
        name="USDT",
        rail=str(Method.CRYPTO),
        is_enabled=True,
        supports_deposit=True,
        supports_withdrawal=True,
        requires_approval=False,
    )
    usdt = MethodCurrency.objects.create(
        method=method, currency="USDT", display_decimals=6, min_amount=Decimal("10")
    )
    MethodNetwork.objects.create(
        asset=usdt,
        code="trc20",
        name="Tron (TRC20)",
        confirmations=20,
        network_fee=Decimal("1"),
        address_pattern=r"T[1-9A-HJ-NP-Za-km-z]{33}",
        position=0,
    )
    MethodNetwork.objects.create(
        asset=usdt,
        code="erc20",
        name="Ethereum (ERC20)",
        confirmations=12,
        network_fee=Decimal("8"),
        address_pattern=r"0x[0-9a-fA-F]{40}",
        position=1,
    )
    return method


@pytest.fixture
def usdt_rate(db: None) -> "ExchangeRate":
    """USDT to USD at parity, with a 1% spread, so conversions are easy to read."""
    return ExchangeRate.objects.create(
        base="USDT",
        quote="USD",
        rate=Decimal("1"),
        margin_percent=Decimal("1"),
        source="test",
    )


@pytest.fixture
def eur_rate(db: None) -> "ExchangeRate":
    """EUR to USD at 1.10, with no spread, so the arithmetic is checkable by hand."""
    return ExchangeRate.objects.create(base="EUR", quote="USD", rate=Decimal("1.10"), source="test")


@pytest.fixture
def funded(alice: Any, cash: "PaymentMethod") -> Any:
    """Alice, with 1000 settled in her wallet, for tests about spending it."""
    from apps.wallet.services import wallet_service

    wallet_service.deposit(alice, amount=Decimal("1000"), method="cash", reference="seed")
    return alice
