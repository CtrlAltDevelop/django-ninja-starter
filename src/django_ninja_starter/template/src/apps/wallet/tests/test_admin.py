"""The admin: configuring the ways to pay, and applying what is waiting.

Two things worth testing here rather than trusting. The screens that configure
money must refuse a configuration that cannot work, and the screens that *move*
money must not be able to move it in any way the API could not -- because an
admin action that bypassed the service would bypass the lock, the funds check and
the state machine with it.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.contrib.admin.sites import AdminSite, site
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.wallet.admin import (
    AdjustmentForm,
    PaymentMethodAdmin,
    WalletAdmin,
    WalletEntryAdmin,
)
from apps.wallet.catalog import MethodCurrency, PaymentMethod
from apps.wallet.errors import WalletError
from apps.wallet.models import (
    Approval,
    EntryKind,
    EntryStatus,
    Wallet,
    WalletEntry,
    WalletStatus,
)
from apps.wallet.services import wallet_service

pytestmark = pytest.mark.django_db


def admin_request(user: Any) -> Any:
    """A request an admin action can actually run against, messages and all."""
    request = RequestFactory().post("/admin/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.fixture
def superuser(db: None) -> Any:
    return get_user_model().objects.create_superuser(
        username="root", email="root@example.test", password="x"
    )


def test_an_entry_cannot_be_added_or_deleted_from_the_admin(superuser: Any) -> None:
    """A movement typed in by hand is a balance change with no lock and no checks.

    Which is every failure mode this app is built to prevent, reintroduced
    through a form -- so the form does not exist, for anybody.
    """
    screen = WalletEntryAdmin(WalletEntry, AdminSite())
    request = admin_request(superuser)

    assert screen.has_add_permission(request) is False
    assert screen.has_delete_permission(request) is False


def test_applying_a_request_from_the_admin_goes_through_the_service(
    alice: Any, counter: PaymentMethod, superuser: Any
) -> None:
    """The same call the API makes, so the same lock is taken and the same rules run."""
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="counter", reference="r")
    screen = WalletEntryAdmin(WalletEntry, AdminSite())
    request = admin_request(superuser)

    screen.approve_entries(request, WalletEntry.objects.filter(pk=entry["id"]))

    applied = WalletEntry.objects.get(pk=entry["id"])
    assert applied.approval == str(Approval.APPROVED)
    assert applied.status == str(EntryStatus.DONE)
    assert applied.reviewed_by_id == superuser.pk


def test_settling_a_payout_from_the_admin_still_checks_the_funds(
    funded: Any, card: PaymentMethod, superuser: Any
) -> None:
    """The check that cannot be skipped: a payout is not recallable.

    The wallet is emptied between recording the withdrawal and settling it, which
    is exactly what a chargeback does -- and the admin refuses, in a message,
    rather than paying out money that is no longer there.
    """
    payout = wallet_service.withdraw(
        funded,
        amount=Decimal("900"),
        method="card",
        reference="w",
        destination="**** 4242",
    )
    # Something else takes the money first.
    wallet_service.withdraw(
        funded, amount=Decimal("100"), method="card", reference="w2", destination="**** 1111"
    )
    wallet = wallet_service.wallet_for(funded)
    wallet.entries.filter(reference="seed").update(amount=Decimal("50"))

    screen = WalletEntryAdmin(WalletEntry, AdminSite())
    request = admin_request(superuser)
    screen.settle_entries(request, WalletEntry.objects.filter(pk=payout["id"]))

    assert WalletEntry.objects.get(pk=payout["id"]).status == str(EntryStatus.PENDING)


def test_enabling_a_method_with_no_currency_is_refused(superuser: Any, db: None) -> None:
    """It would publish a choice that refuses everybody who picks it."""
    unfinished = PaymentMethod.objects.create(
        code="half", name="Half done", rail="cash", is_enabled=False
    )
    ready = PaymentMethod.objects.create(
        code="whole", name="Finished", rail="cash", is_enabled=False
    )
    MethodCurrency.objects.create(method=ready, currency="USD")

    screen = PaymentMethodAdmin(PaymentMethod, AdminSite())
    screen.enable_methods(admin_request(superuser), PaymentMethod.objects.all())

    unfinished.refresh_from_db()
    ready.refresh_from_db()
    assert unfinished.is_enabled is False
    assert ready.is_enabled is True


def test_the_method_list_says_what_a_method_charges(card: PaymentMethod) -> None:
    """Written the way a price list writes it, because that is how a wrong one looks wrong."""
    screen = PaymentMethodAdmin(PaymentMethod, AdminSite())
    rendered = screen.fee_display(card)

    assert "2.9%" in rendered
    assert "processing" in rendered.lower()


def test_an_internal_transfer_method_is_shown_as_free(db: None) -> None:
    screen = PaymentMethodAdmin(PaymentMethod, AdminSite())
    internal = PaymentMethod.objects.create(
        code="internal", name="Wallet to wallet", rail="internal"
    )
    assert screen.fee_display(internal) == "free"


def test_the_seed_command_creates_methods_switched_off(db: None) -> None:
    """A method that arrived by command has decided nothing: no fees, and not enabled."""
    from io import StringIO

    from django.core.management import call_command

    call_command("wallet_methods", stdout=StringIO())

    assert PaymentMethod.objects.exists()
    assert not PaymentMethod.objects.filter(is_enabled=True).exists()
    assert not PaymentMethod.objects.filter(fees__isnull=False).exists()
    # And every one of them asks for a person, until somebody decides otherwise.
    assert not PaymentMethod.objects.filter(requires_approval=False).exists()


def test_the_seed_command_gives_crypto_its_chains(db: None) -> None:
    from io import StringIO

    from django.core.management import call_command

    call_command("wallet_methods", stdout=StringIO())

    usdt = PaymentMethod.objects.get(code="crypto").currencies.get(currency="USDT")
    assert {network.code for network in usdt.networks.all()} == {"erc20", "trc20", "bep20"}


def test_the_seed_command_leaves_a_configured_method_alone(db: None) -> None:
    """So it can be run again after a rail is added, without undoing anybody's work."""
    from io import StringIO

    from django.core.management import call_command

    call_command("wallet_methods", stdout=StringIO())
    PaymentMethod.objects.filter(code="cash").update(is_enabled=True, name="Front desk")
    call_command("wallet_methods", stdout=StringIO())

    kept = PaymentMethod.objects.get(code="cash")
    assert kept.name == "Front desk"
    assert kept.is_enabled is True


# -- the screens actually rendering -------------------------------------------


def test_every_changelist_renders_with_a_row_in_it(
    client: Client, superuser: Any, funded: Any, counter: PaymentMethod, crypto: PaymentMethod
) -> None:
    """Open each of this app's lists with something to draw.

    An empty changelist calls none of the column callables, so a broken column
    renders a clean, reassuring 200 until the first row arrives. Three columns
    here shipped a ``format_html`` with no arguments -- which Django refuses --
    and every one of them was on a screen the tour opened and found empty.

    Parametrised over the registry rather than listed, so a model registered
    tomorrow is covered without anybody remembering to add it here.
    """
    wallet_service.deposit(funded, amount=Decimal("40"), method="counter", reference="waiting")
    wallet_service.withdraw(
        funded, amount=Decimal("25"), method="cash", reference="out", destination="**** 4242"
    )
    client.force_login(superuser)

    for model in site._registry:
        if model._meta.app_label != "wallet":
            continue
        url = reverse(f"admin:wallet_{model._meta.model_name}_changelist")

        response = client.get(url)

        assert response.status_code == 200, f"{url} answered {response.status_code}"


def test_an_amount_is_written_as_money_rather_than_at_storage_precision(
    client: Client, superuser: Any, funded: Any
) -> None:
    """``1000.0000 USD`` is four digits of storage precision presented as meaning."""
    client.force_login(superuser)

    page = client.get(reverse("admin:wallet_wallet_changelist")).content.decode()

    assert "1000.00 USD" in page
    assert "1000.0000" not in page


def test_reversing_from_the_admin_writes_one_correction(
    alice: Any, cash: PaymentMethod, superuser: Any
) -> None:
    """A returned transfer, undone by a person, and a double-click that changes nothing."""
    deposit = wallet_service.deposit(alice, amount=Decimal("100"), method="cash", reference="d")
    screen = WalletEntryAdmin(WalletEntry, AdminSite())

    for _ in range(2):
        screen.reverse_entries(
            admin_request(superuser), WalletEntry.objects.filter(pk=deposit["id"])
        )

    corrections = WalletEntry.objects.filter(counterparty_id=deposit["id"])
    assert corrections.count() == 1
    assert corrections.get().metadata["reversed_by_operator"] == str(superuser.pk)
    assert wallet_service.balance(alice)["settled"] == Decimal("0.0000")


def test_freezing_a_wallet_from_the_admin_goes_through_the_service(
    funded: Any, card: PaymentMethod, superuser: Any
) -> None:
    """The status field is not edited in place; the action calls the service that checks it."""
    screen = WalletAdmin(Wallet, AdminSite())
    wallet = Wallet.objects.get(user=funded)

    screen.freeze_wallets(admin_request(superuser), Wallet.objects.filter(pk=wallet.pk))

    wallet.refresh_from_db()
    assert wallet.status == str(WalletStatus.FROZEN)
    with pytest.raises(WalletError):
        wallet_service.withdraw(
            funded, amount=Decimal("10"), method="card", reference="out", destination="4242"
        )


def test_closing_a_funded_wallet_from_the_admin_is_refused_with_a_message(
    funded: Any, superuser: Any
) -> None:
    """The refusal an operator would get from the API, in the message bar instead of a 500."""
    screen = WalletAdmin(Wallet, AdminSite())
    wallet = Wallet.objects.get(user=funded)

    screen.close_wallets(admin_request(superuser), Wallet.objects.filter(pk=wallet.pk))

    wallet.refresh_from_db()
    assert wallet.status != str(WalletStatus.CLOSED)


def test_an_adjustment_typed_into_the_admin_is_written_by_the_service(
    funded: Any, superuser: Any
) -> None:
    """The one add form in the app, and it still takes the lock and checks the funds."""
    wallet = Wallet.objects.get(user=funded)
    form = AdjustmentForm(
        data={
            "wallet": str(wallet.pk),
            "kind": str(EntryKind.BONUS),
            "amount": "25",
            "description": "Goodwill",
            "idempotency_key": "fixed-key",
        }
    )
    form.operator = superuser

    assert form.is_valid(), form.errors
    assert wallet_service.balance(funded)["settled"] == Decimal("1025.0000")
    assert form.recorded is not None


def test_an_adjustment_the_service_refuses_comes_back_as_a_form_error(
    funded: Any, superuser: Any
) -> None:
    wallet = Wallet.objects.get(user=funded)
    form = AdjustmentForm(
        data={
            "wallet": str(wallet.pk),
            "kind": str(EntryKind.ADJUSTMENT_DEBIT),
            "amount": "5000",
            "description": "Clawback",
            "idempotency_key": "fixed-key",
        }
    )
    form.operator = superuser

    assert form.is_valid() is False
    assert wallet_service.balance(funded)["settled"] == Decimal("1000.0000")
