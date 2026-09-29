"""The nested messages the wallet service answers with.

**Money travels as a string, and that is the whole point.** Protobuf has no
decimal type. A ``double`` would make 19.99 into something that is nearly 19.99,
and a ledger that adds up nearly amounts holds nearly the right balance -- which
is the one property a wallet is not allowed to have. The alternative, minor units
as an integer, needs every client to know the currency's exponent before it can
render anything, and this app holds currencies whose exponent is eight. A string
is exact, is what the REST document carries, and is what every language's decimal
type parses.

The same reasoning covers rates and percentages, which are wider still.

``metadata`` travels as JSON for the reason it does everywhere else here: its
shape belongs to whoever integrated the rail, and there is no protobuf type that
fits every one of them.
"""

from rest_framework import serializers


class Balance(serializers.Serializer[dict[str, object]]):
    """Every number about a wallet at one instant.

    Several rather than one, because a wallet with a pending payout has more than
    one true answer and a client shown a single figure would authorise against
    the friendly one.
    """

    currency = serializers.CharField()
    settled = serializers.CharField()
    available = serializers.CharField()
    incoming = serializers.CharField()
    outgoing = serializers.CharField()
    projected = serializers.CharField()
    has_pending = serializers.BooleanField()
    checkpoint_sequence = serializers.IntegerField()
    unarchived_entries = serializers.IntegerField()


class Wallet(serializers.Serializer[dict[str, object]]):
    id = serializers.CharField()
    currency = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.CharField()
    balance = Balance()


class Charge(serializers.Serializer[dict[str, object]]):
    """One fee, as it was actually charged on this movement."""

    kind = serializers.CharField()
    label = serializers.CharField()
    percent = serializers.CharField()
    amount = serializers.CharField()
    absorbed = serializers.BooleanField()


class Entry(serializers.Serializer[dict[str, object]]):
    """One movement of money, what it cost, and what became of it."""

    id = serializers.CharField()
    kind = serializers.CharField()
    direction = serializers.CharField()
    method = serializers.CharField()
    payment_method = serializers.CharField()
    payment_method_name = serializers.CharField()
    network = serializers.CharField()
    network_name = serializers.CharField()
    amount = serializers.CharField()
    signed_amount = serializers.CharField()
    currency = serializers.CharField()
    wallet_currency = serializers.CharField()
    gross_amount = serializers.CharField()
    fee_total = serializers.CharField()
    net_amount = serializers.CharField()
    charges = Charge(many=True)
    converted = serializers.BooleanField()
    exchange_rate = serializers.CharField()
    destination = serializers.CharField()
    status = serializers.CharField()
    settled = serializers.BooleanField()
    counts_towards_balance = serializers.BooleanField()
    approval = serializers.CharField()
    awaiting_approval = serializers.BooleanField()
    reviewed_at = serializers.CharField()
    review_note = serializers.CharField()
    reference = serializers.CharField()
    external_reference = serializers.CharField()
    description = serializers.CharField()
    metadata = serializers.CharField()
    counterparty_id = serializers.CharField()
    archived = serializers.BooleanField()
    created_at = serializers.CharField()
    settled_at = serializers.CharField()


class Checkpoint(serializers.Serializer[dict[str, object]]):
    """One archived run of entries, and the balance it left behind."""

    id = serializers.CharField()
    sequence = serializers.IntegerField()
    balance = serializers.CharField()
    credited = serializers.CharField()
    debited = serializers.CharField()
    entry_count = serializers.IntegerField()
    created_at = serializers.CharField()


class Network(serializers.Serializer[dict[str, object]]):
    """One chain an asset moves on. A client must never guess at this."""

    code = serializers.CharField()
    name = serializers.CharField()
    confirmations = serializers.IntegerField()
    network_fee = serializers.CharField()
    min_amount = serializers.CharField()
    max_amount = serializers.CharField()
    deposit_address = serializers.CharField()


class Currency(serializers.Serializer[dict[str, object]]):
    currency = serializers.CharField()
    min_amount = serializers.CharField()
    max_amount = serializers.CharField()
    decimals = serializers.IntegerField()
    networks = Network(many=True)


class Fee(serializers.Serializer[dict[str, object]]):
    """One component of what a method charges. Absorbed fees are not published."""

    kind = serializers.CharField()
    label = serializers.CharField()
    applies_to = serializers.CharField()
    percent = serializers.CharField()
    fixed = serializers.CharField()
    basis = serializers.CharField()
    minimum = serializers.CharField()
    maximum = serializers.CharField()
    currency = serializers.CharField()


class Method(serializers.Serializer[dict[str, object]]):
    """One configured way to pay, and everything needed to offer it."""

    code = serializers.CharField()
    name = serializers.CharField()
    rail = serializers.CharField()
    family = serializers.CharField()
    description = serializers.CharField()
    instructions = serializers.CharField()
    icon = serializers.CharField()
    directions = serializers.ListField(child=serializers.CharField())
    settles_immediately = serializers.BooleanField()
    reversible = serializers.BooleanField()
    requires_approval = serializers.BooleanField()
    needs_network = serializers.BooleanField()
    needs_destination = serializers.BooleanField()
    currencies = Currency(many=True)
    fees = Fee(many=True)


class Exchange(serializers.Serializer[dict[str, object]]):
    """One amount converted, and the arithmetic that did it."""

    base = serializers.CharField()
    quote = serializers.CharField()
    rate = serializers.CharField()
    margin_percent = serializers.CharField()
    effective_rate = serializers.CharField()
    amount = serializers.CharField()
    converted = serializers.CharField()
    inverted = serializers.BooleanField()


class Rate(serializers.Serializer[dict[str, object]]):
    base = serializers.CharField()
    quote = serializers.CharField()
    rate = serializers.CharField()
    margin_percent = serializers.CharField()
    source = serializers.CharField()
    effective_from = serializers.CharField()


class Quote(serializers.Serializer[dict[str, object]]):
    """What a movement would cost and produce, before anything is written."""

    method = serializers.CharField()
    method_name = serializers.CharField()
    rail = serializers.CharField()
    direction = serializers.CharField()
    currency = serializers.CharField()
    wallet_currency = serializers.CharField()
    network = serializers.CharField()
    gross = serializers.CharField()
    charges = Charge(many=True)
    fee_total = serializers.CharField()
    absorbed_total = serializers.CharField()
    net = serializers.CharField()
    wallet_amount = serializers.CharField()
    settlement_amount = serializers.CharField()
    converted = serializers.BooleanField()
    exchange = Exchange()
    requires_approval = serializers.BooleanField()
    settles_immediately = serializers.BooleanField()
