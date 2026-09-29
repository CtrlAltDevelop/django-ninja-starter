"""Create a starting set of payment methods for an administrator to finish filling in.

A fresh deployment has no way to pay, because every one of them is a row somebody
has to create. That is correct -- what this deployment takes and what it charges
are not decisions a framework should make -- but it does mean the first hour with
this app is spent typing in the obvious ones.

So this writes the obvious ones: one method per rail this deployment runs, with
its currency, switched **off** and charging **nothing**. What it does not do is
guess at a commission, because a fee invented by a management command is a fee
somebody eventually charges a customer without ever having decided to.

Idempotent. A method whose code already exists is left exactly as it is, so this
can be run again after a rail is added without touching what an administrator has
since configured.
"""

from typing import Any

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.wallet.catalog import MethodCurrency, MethodNetwork, PaymentMethod
from apps.wallet.methods import Direction, Method, enabled_methods

#: The chains an asset is worth offering on, for the crypto rail. Codes only --
#: the fees and the addresses are what the administrator fills in, because those
#: are this deployment's, not this app's.
CHAINS: tuple[tuple[str, str, int], ...] = (
    ("erc20", "Ethereum (ERC20)", 12),
    ("trc20", "Tron (TRC20)", 20),
    ("bep20", "BNB Smart Chain (BEP20)", 15),
)


class Command(BaseCommand):
    help = "Create one switched-off payment method per rail this deployment runs."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--currency",
            default="",
            help="The currency to give each method. Defaults to DJANGO_WALLET_CURRENCY.",
        )
        parser.add_argument(
            "--crypto-assets",
            default="USDT,BTC,ETH",
            help="Which assets to create on the crypto rail, comma separated.",
        )

    @transaction.atomic
    def handle(self, *args: Any, **options: Any) -> None:
        from django.conf import settings

        currency = (options["currency"] or settings.WALLET_CURRENCY).upper()
        assets = [
            code.strip().upper() for code in options["crypto_assets"].split(",") if code.strip()
        ]

        created, skipped = [], []
        for rail, spec in enabled_methods().items():
            code = rail.replace("_", "-")
            if PaymentMethod.objects.filter(code=code).exists():
                skipped.append(code)
                continue
            if rail in {str(Method.INTERNAL), str(Method.SYSTEM)}:
                # Neither is something a customer picks. An internal transfer has
                # its own endpoint and a system entry is written by the app, so a
                # configured method for either would be a choice nobody can take.
                continue

            method = PaymentMethod.objects.create(
                code=code,
                name=spec.label,
                rail=rail,
                description=spec.description,
                supports_deposit=spec.carries(str(Direction.CREDIT)),
                supports_withdrawal=spec.carries(str(Direction.DEBIT)),
                # Off, and requiring approval. Both are the conservative answer,
                # and both are what somebody should have to deliberately change.
                is_enabled=False,
                requires_approval=True,
            )
            for position, asset_code in enumerate(assets if spec.needs_network else [currency]):
                asset = MethodCurrency.objects.create(
                    method=method,
                    currency=asset_code,
                    display_decimals=8 if spec.needs_network else 2,
                    position=position,
                )
                if spec.needs_network:
                    MethodNetwork.objects.bulk_create(
                        [
                            MethodNetwork(
                                asset=asset,
                                code=chain,
                                name=name,
                                confirmations=confirmations,
                                position=index,
                            )
                            for index, (chain, name, confirmations) in enumerate(CHAINS)
                        ]
                    )
            created.append(code)

        for code in created:
            self.stdout.write(f"created {code}")
        if skipped:
            self.stdout.write(f"left alone, already configured: {', '.join(skipped)}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {len(created)} methods, all switched off and charging nothing. "
                "Set the fees and the limits in the admin, then enable the ones you run."
            )
        )
