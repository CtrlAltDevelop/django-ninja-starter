"""The admin, which is where the ways to pay are decided and where requests are applied.

Two jobs, and they belong to different people on different days.

**Configuration.** A method, the currencies it takes, the chains an asset moves
on, what each one charges, and the rates it converts at. Filled in once by
somebody with a contract in front of them, and edited when the contract changes.
These screens are the *only* editing surface: there is no endpoint that creates a
payment method, because deciding what this deployment charges is not a thing a
request should be able to do.

**The queue.** Movements waiting to be applied. A method configured to require
approval turns every deposit through it into a request, and this is where
somebody with a bank statement open decides whether the money really arrived.

Three rules shape the screens.

**A record of money is not editable.** Entries and charges are read-only, all of
them, including for a superuser. What somebody was charged in March is not a
field -- it is what happened, and an admin that lets it be typed over is an admin
that can quietly make the ledger disagree with the bank. Changing where a
movement has got to is done through *actions*, which call exactly the same
service methods the API calls, so the balance check and the state machine cannot
be walked around from here.

**A method is switched off until it is finished.** ``is_enabled`` defaults to
off, and the list screen says out loud when a method is enabled with no currency
configured -- because that one publishes a choice every customer picking it will
be refused at the last step.

**What a fee costs is shown as money, not as a form.** The list renders ``2.9% +
0.30`` rather than four columns somebody has to reassemble in their head, since
the mistake worth catching here is a fee that is a hundred times too big, and
that is only obvious when it is written the way the price list writes it.

The theme is whatever :mod:`apps.wallet.theme` resolves: Unfold where the project
installs it, Django's own admin where it does not.
"""

from typing import Any

from django.contrib import admin, messages
from django.db.models import Count, Q, QuerySet
from django.http import HttpRequest
from django.utils.html import format_html

from apps.wallet.catalog import (
    ExchangeRate,
    MethodCurrency,
    MethodFee,
    MethodNetwork,
    PaymentMethod,
)
from apps.wallet.errors import WalletError
from apps.wallet.models import (
    Approval,
    EntryStatus,
    Wallet,
    WalletCharge,
    WalletCheckpoint,
    WalletEntry,
)
from apps.wallet.money import written
from apps.wallet.services import wallet_service
from apps.wallet.theme import (
    BooleanRadioFilter,
    ChoicesDropdownFilter,
    ModelAdmin,
    TabularInline,
    display,
    dropdown_filter,
)


def _run(request: HttpRequest, call: Any, *args: Any, **kwargs: Any) -> bool:
    """Call a service method, reporting its refusal as a message rather than a 500.

    Every rule this app enforces is a :class:`WalletError`, so an operator who
    tries something the app will not do gets the same sentence a client would --
    in the admin's message bar, where they can read it.
    """
    try:
        call(*args, **kwargs)
    except WalletError as refusal:
        messages.error(request, str(refusal))
        return False
    return True


#: The two colours a warning is written in here, matching the support desk's.
RED = "#b91c1c"
AMBER = "#b45309"


def _badge(colour: str, label: str) -> Any:
    """A coloured, bold cell.

    A helper rather than a `format_html` per site: `format_html` refuses a
    single pre-built string -- rightly, since that is how an f-string full of
    user data gets marked safe -- and three columns here each crashed the
    changelist the moment it had a row to draw.
    """
    return format_html('<b style="color: {}">{}</b>', colour, label)


# -- the configuration -------------------------------------------------------


class MethodNetworkInline(TabularInline):
    """The chains one asset moves on, edited beside it.

    Inline rather than a screen of its own because a network is meaningless
    without its asset: "TRC20" is not a thing you configure, "USDT on TRC20" is.
    """

    model = MethodNetwork
    extra = 0
    fields = (
        "code",
        "name",
        "confirmations",
        "network_fee",
        "deposit_address",
        "address_pattern",
        "min_amount",
        "max_amount",
        "is_enabled",
        "position",
    )


@admin.register(MethodCurrency)
class MethodCurrencyAdmin(ModelAdmin):
    """A currency one method takes, and the chains behind it.

    Registered as a screen of its own as well as being inline on the method,
    because the networks are inline on *this* -- and Django does not nest inlines.
    Configuring USDT on three chains is done here.
    """

    list_display = ("currency", "method", "bounds_display", "network_count", "is_enabled")
    list_filter = ("is_enabled", dropdown_filter("method", None))
    search_fields = ("currency", "method__code", "method__name")
    inlines = [MethodNetworkInline]
    autocomplete_fields = ("method",)

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return (
            super()
            .get_queryset(request)
            .select_related("method")
            .annotate(networks_live=Count("networks", filter=Q(networks__is_enabled=True)))
        )

    @display(description="Limits")
    def bounds_display(self, instance: MethodCurrency) -> str:
        smallest, largest = instance.bounds()
        if not smallest and not largest:
            return "no limit"
        return f"{smallest or '0'} – {largest or 'no ceiling'}"

    @display(description="Chains")
    def network_count(self, instance: MethodCurrency) -> str:
        if not instance.method.needs_network:
            return "—"
        live = getattr(instance, "networks_live", 0)
        # The one configuration mistake that loses money rather than time: an
        # asset on a chain-based rail with no chain configured cannot be paid to
        # at all, and the refusal only shows up at the last step.
        return str(live) if live else _badge(RED, "none")


class MethodCurrencyInline(TabularInline):
    """The currencies a method takes, edited on the method."""

    model = MethodCurrency
    extra = 0
    fields = ("currency", "min_amount", "max_amount", "display_decimals", "is_enabled", "position")
    show_change_link = True


class MethodFeeInline(TabularInline):
    """What a method charges, in the order the charges are applied.

    Order is load-bearing rather than cosmetic: a tax whose basis is "the charges
    before it" reads whatever has been worked out so far, so a VAT row positioned
    above the commission it taxes is a tax on nothing.
    """

    model = MethodFee
    extra = 0
    fields = (
        "position",
        "kind",
        "label",
        "applies_to",
        "currency",
        "percent",
        "fixed",
        "basis",
        "minimum",
        "maximum",
        "absorbed",
        "is_enabled",
    )


@admin.register(PaymentMethod)
class PaymentMethodAdmin(ModelAdmin):
    """One configured way to pay: what it is, what it takes, and what it costs.

    The screen this app is configured from. The rail is picked first and decides
    everything that is not on this form -- whether a movement through it settles
    at once, whether it can be charged back, whether it needs a chain and an
    address -- and the rest of the fields are the commercial decisions.
    """

    list_display = (
        "name",
        "code",
        "rail",
        "directions_display",
        "currencies_display",
        "fee_display",
        "approval_display",
        "is_enabled",
    )
    list_filter = (
        dropdown_filter("is_enabled", BooleanRadioFilter),
        dropdown_filter("rail", ChoicesDropdownFilter),
        "requires_approval",
        "supports_deposit",
        "supports_withdrawal",
    )
    search_fields = ("code", "name", "description")
    prepopulated_fields = {"code": ("name",)}
    inlines = [MethodCurrencyInline, MethodFeeInline]
    actions = ["enable_methods", "disable_methods"]
    fieldsets = (
        (None, {"fields": ("name", "code", "rail", "is_enabled", "position")}),
        (
            "What it does",
            {
                "fields": ("supports_deposit", "supports_withdrawal", "requires_approval"),
                "description": (
                    "Requires approval turns every movement through this method into a "
                    "request an operator has to apply before it can settle. Leave it on "
                    "unless something outside this app confirms the money independently."
                ),
            },
        ),
        (
            "What a customer sees",
            {"fields": ("description", "instructions", "icon")},
        ),
        (
            "Fallback limits",
            {
                "fields": ("min_amount", "max_amount"),
                "description": (
                    "Only used by currencies that set none of their own. A single limit "
                    "across dollars and bitcoin is not a limit, so the real ones belong "
                    "on each currency."
                ),
                "classes": ("collapse",),
            },
        ),
        ("Integration", {"fields": ("config",), "classes": ("collapse",)}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return (
            super()
            .get_queryset(request)
            .prefetch_related("currencies", "fees")
            .annotate(live_currencies=Count("currencies", filter=Q(currencies__is_enabled=True)))
        )

    @display(description="Moves")
    def directions_display(self, instance: PaymentMethod) -> str:
        ways = [
            label
            for label, on in (
                ("in", instance.supports_deposit),
                ("out", instance.supports_withdrawal),
            )
            if on
        ]
        return " & ".join(ways) or "—"

    @display(description="Currencies")
    def currencies_display(self, instance: PaymentMethod) -> str:
        live = [row.currency for row in instance.currencies.all() if row.is_enabled]
        if live:
            return ", ".join(sorted(live))
        # An enabled method with nothing to price is published to clients and
        # refuses every customer who picks it, which is worse than being absent.
        if instance.is_enabled:
            return _badge(RED, "none — this method cannot be used")
        return "none yet"

    @display(description="Charges")
    def fee_display(self, instance: PaymentMethod) -> str:
        """What this costs, written the way a price list writes it.

        Assembled into one string rather than left as four columns, because the
        mistake worth catching is a percentage that is a hundred times too large,
        and that is only obvious next to the ``%``.
        """
        if not instance.chargeable:
            return "free"
        parts = []
        for fee in instance.fees.all():
            if not fee.is_enabled:
                continue
            bits = []
            if fee.percent:
                bits.append(f"{fee.percent.normalize()}%")
            if fee.fixed:
                bits.append(f"{fee.fixed.normalize()}")
            rendered = " + ".join(bits)
            parts.append(f"{rendered} ({fee.display_label.lower()})")
        return "; ".join(parts) or "free"

    @display(description="Approval", boolean=True)
    def approval_display(self, instance: PaymentMethod) -> bool:
        return instance.requires_approval

    @admin.action(description="Enable the selected methods")
    def enable_methods(self, request: HttpRequest, queryset: QuerySet) -> None:
        """Switch methods on, skipping the ones that are not finished.

        A method with no enabled currency cannot price anything, so enabling it
        publishes a choice that refuses everyone who takes it. Refused here rather
        than discovered by a customer.
        """
        ready: list[PaymentMethod] = []
        unfinished: list[PaymentMethod] = []
        for method in queryset:
            target = ready if method.currencies.filter(is_enabled=True).exists() else unfinished
            target.append(method)
        if ready:
            changed = PaymentMethod.objects.filter(pk__in=[method.pk for method in ready]).update(
                is_enabled=True
            )
            messages.success(request, f"Enabled {changed}.")
        if unfinished:
            messages.warning(
                request,
                "Left off, because they have no currency configured and could not price "
                f"anything: {', '.join(method.name for method in unfinished)}.",
            )

    @admin.action(description="Disable the selected methods")
    def disable_methods(self, request: HttpRequest, queryset: QuerySet) -> None:
        messages.success(request, f"Disabled {queryset.update(is_enabled=False)}.")


@admin.register(MethodFee)
class MethodFeeAdmin(ModelAdmin):
    """Every fee across every method, for the day somebody asks what we charge."""

    list_display = (
        "method",
        "kind",
        "label",
        "applies_to",
        "cost_display",
        "absorbed",
        "is_enabled",
    )
    list_filter = ("kind", "applies_to", "absorbed", "is_enabled", dropdown_filter("method", None))
    search_fields = ("label", "method__code", "method__name")
    autocomplete_fields = ("method", "currency")

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return super().get_queryset(request).select_related("method", "currency")

    @display(description="Costs")
    def cost_display(self, instance: MethodFee) -> str:
        bits = []
        if instance.percent:
            bits.append(
                f"{instance.percent.normalize()}% of {instance.get_basis_display().lower()}"
            )
        if instance.fixed:
            bits.append(f"{instance.fixed.normalize()} flat")
        rendered = " + ".join(bits)
        if instance.minimum or instance.maximum:
            rendered += f" (between {instance.minimum or 0} and {instance.maximum or 'no cap'})"
        return rendered


@admin.register(ExchangeRate)
class ExchangeRateAdmin(ModelAdmin):
    """What one currency is worth in another, and the spread kept on it.

    Rows are added rather than edited: a new row with a later ``effective_from``
    supersedes the one before it, and the old one stays as the answer to "what did
    we convert at, on the day we converted?". A conversion that cannot be
    explained six months later is a conversion that gets disputed.
    """

    list_display = (
        "pair_display",
        "rate",
        "margin_percent",
        "source",
        "effective_from",
        "is_enabled",
    )
    list_filter = ("is_enabled", "base", "quote")
    search_fields = ("base", "quote", "source")
    date_hierarchy = "effective_from"

    @display(description="Pair")
    def pair_display(self, instance: ExchangeRate) -> str:
        return f"{instance.base}/{instance.quote}"


# -- the money ---------------------------------------------------------------


class WalletChargeInline(TabularInline):
    """What a movement was charged, as it was charged. Read-only, permanently."""

    model = WalletCharge
    extra = 0
    can_delete = False
    fields = ("kind", "label", "percent", "amount", "absorbed")
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class WalletEntryInline(TabularInline):
    """A wallet's recent movements, on the wallet. Read-only, like every record here."""

    model = WalletEntry
    extra = 0
    can_delete = False
    fields = ("created_at", "kind", "method", "amount", "status", "approval", "reference")
    readonly_fields = fields
    ordering = ("-created_at",)
    # A long-lived wallet has thousands of these, and the inline would render
    # every one of them. The full history is a click away on the entry list.
    max_num = 25

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return super().get_queryset(request).select_related("wallet")


@admin.register(Wallet)
class WalletAdmin(ModelAdmin):
    """One account's wallet, its balance, and what it has done.

    The balance columns are computed rather than stored -- that is the whole
    design of this app -- so they cost a query each. Acceptable on a screen
    somebody opens to look at one account; the listing pages twenty at a time.
    """

    list_display = (
        "user",
        "currency",
        "status",
        "settled_display",
        "available_display",
        "created_at",
    )
    list_filter = (dropdown_filter("status", ChoicesDropdownFilter), "currency")
    search_fields = ("user__username", "user__email", "id")
    readonly_fields = ("id", "created_at", "updated_at", "balance_display")
    inlines = [WalletEntryInline]
    fields = ("user", "currency", "status", "balance_display", "id", "created_at", "updated_at")
    autocomplete_fields = ("user",)

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return super().get_queryset(request).select_related("user")

    def _balance(self, instance: Wallet) -> Any:
        from apps.wallet.balances import balance_of

        return balance_of(instance)

    @display(description="Settled")
    def settled_display(self, instance: Wallet) -> str:
        return written(self._balance(instance).settled, instance.currency)

    @display(description="Available")
    def available_display(self, instance: Wallet) -> str:
        return written(self._balance(instance).available, instance.currency)

    @display(description="Balance")
    def balance_display(self, instance: Wallet) -> str:
        """Every number at once, because a wallet screen that shows one is a trap.

        Settled is what is there; available is what may be spent, which is less
        whenever a payout is pending; projected is where it lands if everything
        outstanding succeeds, and is never what to authorise against.
        """
        if instance.pk is None:
            return "—"
        balance = self._balance(instance)
        return format_html(
            "<b>{} settled</b> · {} available · {} incoming · {} outgoing · {} projected",
            written(balance.settled, instance.currency),
            written(balance.available, instance.currency),
            written(balance.incoming, instance.currency),
            written(balance.outgoing, instance.currency),
            written(balance.projected, instance.currency),
        )


@admin.register(WalletEntry)
class WalletEntryAdmin(ModelAdmin):
    """Every movement, and the queue of the ones waiting on a person.

    Entirely read-only as *fields*. Everything that changes a movement is an
    action, and every action calls the service the API calls -- so approving a
    payout from here re-checks the balance under the wallet's lock exactly as a
    webhook would, and there is no way to move money from this screen that the
    rest of the app would have refused.
    """

    list_display = (
        "created_at",
        "wallet",
        "kind",
        "method_display",
        "amount_display",
        "cost_display",
        "status",
        "approval_display",
    )
    list_filter = (
        dropdown_filter("status", ChoicesDropdownFilter),
        dropdown_filter("approval", ChoicesDropdownFilter),
        dropdown_filter("kind", ChoicesDropdownFilter),
        dropdown_filter("method", ChoicesDropdownFilter),
        ("payment_method", admin.RelatedOnlyFieldListFilter),
    )
    search_fields = (
        "reference",
        "external_reference",
        "destination",
        "wallet__user__username",
        "wallet__user__email",
    )
    date_hierarchy = "created_at"
    inlines = [WalletChargeInline]
    actions = ["approve_entries", "reject_entries", "settle_entries", "fail_entries"]
    readonly_fields = (
        "id",
        "wallet",
        "kind",
        "direction",
        "method",
        "payment_method",
        "network",
        "amount",
        "currency",
        "gross_amount",
        "fee_total",
        "exchange_rate",
        "destination",
        "status",
        "approval",
        "reviewed_by",
        "reviewed_at",
        "review_note",
        "reference",
        "external_reference",
        "description",
        "metadata",
        "counterparty",
        "checkpoint",
        "created_at",
        "updated_at",
        "settled_at",
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return (
            super()
            .get_queryset(request)
            .select_related("wallet", "wallet__user", "payment_method", "network")
        )

    def has_add_permission(self, request: HttpRequest) -> bool:
        """No. A movement is written by the service that checked it, or not at all.

        An entry typed in by hand is a balance change with no lock taken, no funds
        checked and no idempotency key -- which is every failure mode this app is
        built to prevent, reintroduced through a form.
        """
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """No. Undoing a settled movement is a reversing entry, never a deletion."""
        return False

    @display(description="Via")
    def method_display(self, instance: WalletEntry) -> str:
        if instance.payment_method_id:
            name = instance.payment_method.name
            return f"{name} · {instance.network.name}" if instance.network_id else name
        return instance.get_method_display()

    @display(description="Amount")
    def amount_display(self, instance: WalletEntry) -> str:
        sign = "+" if instance.direction == "credit" else "−"
        rendered = f"{sign}{written(instance.amount, instance.wallet.currency)}"
        if instance.converted:
            rendered += f" (from {written(instance.gross_amount, instance.currency)})"
        return rendered

    @display(description="Charged")
    def cost_display(self, instance: WalletEntry) -> str:
        return written(instance.fee_total, instance.wallet.currency) if instance.fee_total else "—"

    @display(description="Approval")
    def approval_display(self, instance: WalletEntry) -> str:
        if instance.approval == str(Approval.NOT_REQUIRED):
            return "—"
        if instance.awaiting_approval:
            return _badge(AMBER, "waiting")
        return instance.get_approval_display()

    @admin.action(description="Apply the selected requests")
    def approve_entries(self, request: HttpRequest, queryset: QuerySet) -> None:
        """Approve each request, settling the ones whose rail needs no confirmation.

        One at a time, through the service, so each takes its own wallet's lock
        and is checked on its own terms. A bulk update would be faster and would
        skip every check that makes this safe.
        """
        applied = sum(
            1
            for entry in queryset.awaiting_approval()
            if _run(request, wallet_service.approve, entry.pk, by=request.user)
        )
        messages.success(request, f"Applied {applied}.")

    @admin.action(description="Refuse the selected requests")
    def reject_entries(self, request: HttpRequest, queryset: QuerySet) -> None:
        refused = sum(
            1
            for entry in queryset.awaiting_approval()
            if _run(request, wallet_service.reject, entry.pk, by=request.user)
        )
        messages.success(request, f"Refused {refused}.")

    @admin.action(description="Settle the selected movements")
    def settle_entries(self, request: HttpRequest, queryset: QuerySet) -> None:
        """Confirm that the money really moved. This is where a balance changes.

        Refuses anything still waiting on approval, and re-checks the funds behind
        every payout, because between recording and confirming a chargeback may
        have taken the money away.
        """
        settled = sum(
            1
            for entry in queryset.filter(status=str(EntryStatus.PENDING))
            if _run(
                request,
                wallet_service.settle,
                entry.wallet.user,
                entry.pk,
            )
        )
        messages.success(request, f"Settled {settled}.")

    @admin.action(description="Mark the selected movements failed")
    def fail_entries(self, request: HttpRequest, queryset: QuerySet) -> None:
        failed = sum(
            1
            for entry in queryset.filter(status=str(EntryStatus.PENDING))
            if _run(
                request,
                wallet_service.fail,
                entry.wallet.user,
                entry.pk,
                reason="Marked failed in the admin.",
            )
        )
        messages.success(request, f"Failed {failed}.")


@admin.register(WalletCheckpoint)
class WalletCheckpointAdmin(ModelAdmin):
    """The archive: each folded run of entries and the balance it left behind.

    Read-only and visible, because it is what makes a derived balance checkable.
    A reader can add the checkpoints up and get the same number the app reports,
    which is the point of publishing it at all.
    """

    list_display = (
        "wallet",
        "sequence",
        "balance",
        "credited",
        "debited",
        "entry_count",
        "created_at",
    )
    search_fields = ("wallet__user__username", "wallet__id")
    date_hierarchy = "created_at"
    readonly_fields = (
        "wallet",
        "sequence",
        "balance",
        "credited",
        "debited",
        "entry_count",
        "created_at",
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return super().get_queryset(request).select_related("wallet", "wallet__user")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """No. A checkpoint is cut by `manage.py wallet_archive`, under a lock."""
        return False
