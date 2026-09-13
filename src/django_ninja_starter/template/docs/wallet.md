# Wallet

A wallet per account, every way money gets in and out, and a balance derived
from the movements rather than stored beside them. Optional in the same way
every login method is: naming it in `DJANGO_WALLET_ENABLED` is what installs it,
and a project that does not name it carries no wallet tables, no routes and
never imports the package.

Like the [CMS](cms.md), [notifications](notifications.md), the [shop](shop.md)
and [support](support.md), it is a **feature app**: it lives in
`src/apps/wallet`, and the directory can be copied into another Django project
or deleted from this one without leaving a hole. Its own
[`README`](../src/apps/wallet/README.md) is the drop-it-in-elsewhere guide.

## There is no balance column

That is the one decision everything else follows from, and it is worth saying
why, because a column is the obvious design and it is wrong in a way that is
expensive to discover.

A stored balance is a second copy of a fact the movements already hold. The two
disagree the first time a process dies between writing a movement and updating
the column — or the first time two requests read the same balance, add to it,
and write, and one of the two additions is simply gone. Recovering from that
means recomputing from the movements, which is to say: the movements were the
balance all along.

So the balance is derived, and three tables make that affordable.

A **wallet** is the account's identity here: one per account, its currency, and
whether it may move at all.

An **entry** is one movement — an amount, a direction, the rail it came in on,
and a *status*. A pending entry is not money.

A **checkpoint** is an archived run of entries collapsed into the balance they
left. Summing every entry a wallet has ever had is correct and gets slower
forever, so a scheduled command folds settled entries into a checkpoint and the
balance becomes **the last checkpoint, plus the entries written since it**.

Reading a balance is therefore one indexed row and a bounded aggregate, whether
the wallet is a day old or five years old.

## Always more than one number

A wallet holding 100, with a pending withdrawal of 40 and a pending deposit of
25, has several defensible "balances". An API that picked one would be picking
wrong for somebody, so all of them are named:

| Field | What it is | What it is for |
| --- | --- | --- |
| `settled` | Everything confirmed | What the wallet actually holds |
| `incoming` | Pending, inbound | Visible, and worth nothing |
| `outgoing` | Pending, outbound | Already spoken for |
| `available` | `settled` − `outgoing` | **Authorise against this one** |
| `projected` | `settled` + `incoming` − `outgoing` | Show it; never authorise on it |

`available` is the one that matters. Money behind a withdrawal that has not left
yet is spoken for, and letting it be promised twice means choosing later which
of the two payouts to fail.

## Status is what the money is doing

An entry that has been recorded is not money that has arrived. A card deposit is
pending until the processor confirms it, and a pending entry counted as balance
is money the wallet does not have.

| `status` | Means | Counts as balance |
| --- | --- | --- |
| `pending` | Recorded, waiting on something outside this app | No |
| `done` | Settled | **Yes** |
| `failed` | The rail refused it | No |
| `cancelled` | Withdrawn before it settled | No |
| `expired` | Never confirmed inside the window | No |
| `reversed` | It settled and was then undone | **Yes** — see below |

Reversal is a state change **plus an opposing entry**, never an edit of the
original. The original really did happen, and a ledger that rewrites what
happened cannot be reconciled against the rail that still remembers it.

Which is exactly why a reversed entry goes on counting. If marking the original
`reversed` also took it out of the balance, the opposing entry would take the
same money away a second time and the wallet would end up short by the amount of
the original — permanently, with nothing afterwards to say why. Read `reversed`
as *"this happened, and its counterpart happened too"*, not as *"this did not
happen"*. It keeps the moment it settled, too: "when did this clear?" starts
being asked the day a chargeback lands, not before.

So an entry publishes **both** answers. `settled` is true only for `done`;
`counts_towards_balance` is true for `done` and `reversed`. Add the
`signed_amount` of every movement where the second is set and you get `settled`
from the balance — which is the whole contract, and the reason a client is not
asked to infer it from a status.

## Approval is what a person decided

A second axis, and genuinely independent of the first: a deposit can be waiting
on the bank *and* waiting on the back office at the same time, and collapsing
the two into one field forces a choice about which fact to lose.

| `approval` | Means |
| --- | --- |
| `not_required` | The method does not ask for one |
| `requested` | The account asked. It is a request until somebody applies it |
| `approved` | An operator applied it. It may settle when the money does |
| `rejected` | An operator refused it, and it was cancelled with the same stroke |

The rule is one sentence: **a movement awaiting approval cannot settle.** It can
still fail, be cancelled or expire — refusing money is never the dangerous
direction.

`requires_approval` is **on** by default for a new method, because the
alternative default is money moving on an unverified claim. A cash deposit
somebody says they made needs a person; a card capture confirmed by a webhook
does not.

## The ways to pay are configured, not compiled

Two halves, and keeping them apart is what makes the rest tractable.

**The rail is a type, declared in code.** A card clears in seconds and can be
charged back for months; a bank transfer takes a day and is final. Those are
facts about cards and banks, not about a deployment, so they live in
`apps/wallet/methods.py`, are reviewed in code, and cannot be edited by somebody
filling in a form at two in the morning.

**The method is a row, filled in by an administrator.** *Which* processor this
deployment runs, what it charges, which currencies it takes, whether a movement
through it needs a person — commercial decisions that change without a release.

A configured method always points at a rail and inherits its physics. Nothing in
the admin can make a card irreversible or a cash payment asynchronous.

| Table | What it holds |
| --- | --- |
| `PaymentMethod` | One configured way to pay, and its enable flag |
| `MethodCurrency` | A currency it takes, with the limits that apply *in that currency* |
| `MethodNetwork` | For crypto: the chain an asset moves on |
| `MethodFee` | One component of what it costs |
| `ExchangeRate` | What one currency is worth in another, and the spread kept |

`GET /wallet/methods` is the only place a client can learn any of this. A client
that hard-codes a list of ways to pay breaks the afternoon somebody turns one
on.

Each method also carries a `family` — `cash`, `bank`, `card`, `wallet`, `crypto`,
`voucher` — which is what a customer recognises and what a client groups by. It
is coarser than the rail on purpose: somebody choosing "Bank" does not care
whether the operator configured SEPA, ACH or a wire behind it.

### Limits belong to a currency

A ten-unit minimum is a sensible card floor in dollars and a fortune in bitcoin,
so `min_amount` and `max_amount` live on the currency row. The method carries
fallbacks for currencies that set none. Zero means "no bound" at every level —
a deployment that has not thought about a ceiling gets no ceiling, rather than
one this app invented.

### Crypto: the chain is never guessed at

USDT on Ethereum and USDT on Tron are one balance to a customer and two
incompatible destinations to the network. Paying to the wrong one does not fail:
it confirms, and the money is gone to an address nobody holds a key for.

So the chain is a separate row with its own fee, its own confirmation count and
its own address pattern; a payout must name it; and an address that does not
match that chain's shape is **refused before it is sent**, because the chain
will not refuse it.

`network` is never defaulted, even when an asset has exactly one chain
configured today. A default that is right once is wrong the moment a second
chain is added.

## What a movement costs

A fee is `percent` of its basis, plus `fixed`, held between `minimum` and
`maximum`. Any of the four can be zero, which is how the usual shapes are
expressed: percentage-only, flat-only, or percentage with a floor.

Charges are kept apart by `kind` — commission, tax, cost, network, service —
because they are owed to different people. A commission is revenue, a tax is
collected on somebody else's behalf, a network fee is paid to a chain and passed
through. Collapsing them into one number is fine until somebody has to file a
return.

**`basis` is the one worth understanding.** A percentage is normally of the
amount. Set it to `charges` and it is a percentage of the fees worked out before
it — which is how VAT on a payment commission actually works in most of Europe.
A deployment that could only express the first would overcharge every customer
it has.

**`absorbed`** marks a cost this deployment pays out of its own margin. It is
calculated and recorded so the reporting adds up, and it changes nothing about
what the customer receives. Absorbed fees are not published in the price list,
because they are not a price.

### The rule for fees is one sentence

**Charges come out of the amount the customer named.**

A deposit of 100 with 3.84 in fees credits 96.16. A withdrawal of 100 with 3.84
in fees debits 100 and pays out 96.16. Uniform in both directions, so a limit
means the same thing whichever way money is going, and the figure the customer
typed is the figure they recognise on the record.

### The same function quotes and charges

`POST /wallet/quotes` prices a movement without writing anything, and the
recording path calls exactly the same function. An amount quoted is the amount
charged. A quote endpoint that re-implemented the arithmetic would one day
differ by a cent, and no amount of apologising makes that not a bug.

## Transfers between wallets here are free

No commission, no tax, no fixed cost, no spread — and **no way to add one**. The
money never leaves this app, so nothing was spent moving it, and there is
nothing to pass on. A fee saved against the internal rail is refused at the point
somebody tries to store it, so this is a property of the app rather than a
default that an afternoon in the admin could quietly change.

The amount that leaves one wallet is the amount that arrives in the other,
always. Both sides are written in one transaction and settle at once: there is
no rail in the middle to wait for, and nothing to approve.

## Conversion, and the side the spread falls on

A movement can be named in a currency the wallet is not held in. It is charged in
that currency, then converted at the live rate — in that order, which matters,
because a *fixed* fee of 0.30 is 0.30 euros rather than 0.30 dollars.

`margin_percent` is the spread, and it is always taken against the customer:
money coming in converts at slightly under the rate, money going out at slightly
over. It is published beside the rate rather than folded into it. A deployment
keeping a spread is entitled to one; a deployment that will not say it is
keeping one is a different thing, and this app does not help with that.

A pair quoted one way converts both ways off its reciprocal, so nobody has to
keep `EUR/USD` and `USD/EUR` in step by hand. A pair with no rate is **refused**
rather than converted at one.

Rate rows are never edited, only superseded: a new row with a later
`effective_from` becomes live, and the old one stays as the answer to "what did
we convert at, on the day we converted?"

### Applying a request is all or nothing

Approving a movement and settling it happen in one transaction. If the
settlement is refused — a payout whose money went while the request sat in the
queue — the approval goes back with it and the movement stays a request.

That is deliberate, and it is the safer of the two designs: the alternative
leaves a payout marked *approved* but unsettled, which is a standing
authorisation that would fire the moment the balance recovered, with nobody
looking at it a second time.

A movement that has already reached a terminal status — the account cancelled
it, the window expired — cannot be applied or refused at all, even though its
approval is still `requested`. It keeps that for the record and drops out of the
queue, because a queue holding rows nobody can clear is a queue people stop
reading.

## Racing for the same money

Two withdrawals of eight against a balance of ten. Both read the balance, both
see enough, both write — and the wallet ends at minus six.

The defence is not cleverer arithmetic. **Every write that depends on a balance
takes `select_for_update` on the wallet row first**, so the second transaction
blocks until the first commits and then sees two. A transfer locks both wallets,
always in primary-key order, because two transfers crossing in opposite
directions would otherwise take the two locks in opposite orders and deadlock.

Pricing happens *outside* the lock, deliberately: it reads configuration and does
arithmetic, it can block nobody, and doing it inside would hold a row lock across
queries that have nothing to do with that row.

**This needs a database with row locks.** `select_for_update` compiles to nothing
on SQLite, so the starter's default database gives you the code path and none of
the protection — which is fine for development and is not a thing to hold real
money on. Run a wallet on PostgreSQL or MySQL. The tests say the same thing out
loud: the racing test in `tests/test_concurrency.py` skips itself rather than
passing on a backend where it proves nothing.

**A retry is not a second movement.** Every write takes a `reference`, unique per
wallet, and a call carrying one that already exists returns the entry that
already exists. That is what makes a client safe to retry on a timeout, which is
the one thing a payment client will certainly do.

## The archive

`manage.py wallet_archive` folds entries that can no longer change into a
checkpoint carrying the running balance, and stamps each folded entry with it.
Run it daily.

Two triggers, and either is enough: an **age**, so a day's entries are folded on
the next run; and a **count** — `DJANGO_WALLET_ARCHIVE_THRESHOLD`, twenty by
default — so a wallet doing a hundred movements an hour does not wait a day with
a hundred rows to re-sum on every read.

**A pending entry is never archived, however old.** It is still free to change,
and a checkpoint is a number written down; folding one in would make that written
number wrong later. So an old pending entry holds its own run back, and the
command reports what it skipped.

Each checkpoint carries the *running* balance rather than the sum of what it
folded, so reading a balance is one row plus whatever has happened since, rather
than a walk back through every checkpoint ever cut. `sequence` is per wallet and
gapless, which is what makes a missing checkpoint visible.

The entries are not deleted. They are stamped with the checkpoint that counted
them, so history stays complete and no entry can be counted twice.

## Getting started

```bash
DJANGO_WALLET_ENABLED=true python3 manage.py migrate
DJANGO_WALLET_ENABLED=true python3 manage.py wallet_methods
```

`wallet_methods` creates one method per rail this deployment runs, each switched
**off** and charging **nothing**. It deliberately does not guess at a commission:
a fee invented by a management command is a fee somebody eventually charges a
customer without ever having decided to. Fill in the currencies and the fees in
the admin, then enable the ones you actually run.

It is idempotent — a method whose code already exists is left exactly as it is —
so it can be run again after a rail is added.

## Three transports, one set of rules

HTTP, GraphQL and gRPC, publishing the same operations. None of them decides
anything: every rule lives in `services.py`, so a wallet cannot have two answers
to "may I withdraw this?" — and the wrong one is always the one somebody found.

`DJANGO_WALLET_TRANSPORTS` picks which doors a deployment opens. Naming none
opens all three.

Three things are deliberately **not** on any account's transport.

**Nothing creates a payment method.** Deciding what this deployment charges is
not something a request should be able to do, so the catalogue is written in the
admin and nowhere else.

**Nothing applies a request.** Approval crosses wallets and its authorisation is
the back office's, so it lives on the admin screens and on
`WalletService.approve` — where a project that wants to publish it can reach it
behind its own permissions rather than inheriting an endpoint it never asked for.

**Nothing reports that money moved.** Settling, failing, expiring and reversing
are statements about a world this app cannot see, and an account is not a witness
to it: a customer who could confirm their own deposit would be running a mint,
and one who could reverse their own paid-out withdrawal would be paid twice. So
those verbs are published to nobody. They arrive from the rail, signed, or from
an operator in the admin.

The one lifecycle verb an account does get is **cancel**, because giving up on a
payment you started asserts nothing about whether money moved.

## Confirming a movement

A pending movement settles one of two ways, and neither of them is the customer.

**The rail's webhook.** `POST /api/v1/wallet/hooks/{method}`, with two headers:

    X-Wallet-Timestamp: 1757764800
    X-Wallet-Signature: 8f4c…          hex HMAC-SHA256

    signature = HMAC-SHA256(secret, f"{timestamp}.{raw body}")

and a body naming the movement and what happened:

```json
{"entry_id": "…", "event": "done", "external_reference": "ch_3Qx"}
```

`done` or `failed` — the only two things a rail knows. The secret is per method,
from `DJANGO_WALLET_WEBHOOK_SECRETS=stripe-card:whsec_…,coinbase:…`: per method
because the secrets belong to different companies and one that leaks should not
confirm movements on another's rail, and from the environment because a secret in
a column is a secret in every backup and on an admin screen.

Everything unverifiable gets one `401` and one sentence — unsigned, wrongly
signed, stale beyond `DJANGO_WALLET_WEBHOOK_TOLERANCE_SECONDS`, or a method with
no secret configured — because an endpoint that distinguished them would tell
whoever is probing it which half they had right. The signature covers the
timestamp, so a confirmation captured off the wire is worthless tomorrow.

A method with **no** secret confirms nothing. That is the safe direction to fail:
its movements wait for an operator, which looks like a queue somebody notices
rather than an open door.

**An operator in the admin.** For a rail with no webhook at all — cash over a
counter, a bank transfer read off a statement — `Movements → Apply` and
`Settle` do the same thing behind a person. A movement waiting on approval stays
waiting however loudly its processor confirms it: the rail's confirmation and the
operator's approval are separate facts, and the webhook can only supply the first.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/wallet` | Bearer | This account's wallet and its balance |
| `GET` | `/api/v1/wallet/balance` | Bearer | What is there, and what is on its way |
| `GET` | `/api/v1/wallet/checkpoints` | Bearer | The archive the balance is read from |
| `POST` | `/api/v1/wallet/deposits` | Bearer | Record money arriving |
| `GET` | `/api/v1/wallet/entries` | Bearer | Every movement this wallet has had |
| `GET` | `/api/v1/wallet/entries/{entry_id}` | Bearer | Read one movement |
| `POST` | `/api/v1/wallet/entries/{entry_id}/cancel` | Bearer | Withdraw it before it lands |
| `GET` | `/api/v1/wallet/exchange` | Bearer | Convert an amount between currencies |
| `POST` | `/api/v1/wallet/hooks/{method_code}` | None | A payment rail confirming one movement |
| `GET` | `/api/v1/wallet/methods` | Bearer | The ways to pay this deployment offers |
| `GET` | `/api/v1/wallet/methods/{code}` | Bearer | One way to pay, in full |
| `POST` | `/api/v1/wallet/quotes` | Bearer | What a movement would cost |
| `GET` | `/api/v1/wallet/rates` | Bearer | The conversion rates in force |
| `POST` | `/api/v1/wallet/transfers` | Bearer | Move money to another account's wallet |
| `POST` | `/api/v1/wallet/withdrawals` | Bearer | Record money leaving |
<!-- /generated:routes -->

## Models

<!-- generated:models -->
#### `ExchangeRate`

What one unit of ``base`` is worth in ``quote``, and what this deployment keeps.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `base` | Char |  |
| `quote` | Char |  |
| `rate` | Decimal |  |
| `margin_percent` | Decimal |  |
| `source` | Char |  |
| `is_enabled` | Boolean |  |
| `effective_from` | DateTime |  |
| `created_at` | DateTime | not editable |

#### `MethodCurrency`

A currency one method takes, and the limits that actually apply.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `method` | ForeignKey | → `wallet.PaymentMethod` |
| `currency` | Char |  |
| `min_amount` | Decimal |  |
| `max_amount` | Decimal |  |
| `display_decimals` | PositiveSmallInteger |  |
| `is_enabled` | Boolean |  |
| `position` | Integer |  |

#### `MethodFee`

One component of what a movement costs, and how it is worked out.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `method` | ForeignKey | → `wallet.PaymentMethod` |
| `currency` | ForeignKey | → `wallet.MethodCurrency`, nullable |
| `kind` | Char |  |
| `label` | Char |  |
| `applies_to` | Char |  |
| `percent` | Decimal |  |
| `fixed` | Decimal |  |
| `basis` | Char |  |
| `minimum` | Decimal |  |
| `maximum` | Decimal |  |
| `absorbed` | Boolean |  |
| `is_enabled` | Boolean |  |
| `position` | Integer |  |

#### `MethodNetwork`

One chain an asset moves on: the fee, the confirmations, and the address shape.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `asset` | ForeignKey | → `wallet.MethodCurrency` |
| `code` | Slug |  |
| `name` | Char |  |
| `confirmations` | PositiveInteger |  |
| `address_pattern` | Char |  |
| `network_fee` | Decimal |  |
| `min_amount` | Decimal |  |
| `max_amount` | Decimal |  |
| `deposit_address` | Char |  |
| `explorer_url` | Char |  |
| `is_enabled` | Boolean |  |
| `position` | Integer |  |

#### `PaymentMethod`

One configured way to move money, and everything a client needs to offer it.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `code` | Slug | unique |
| `name` | Char |  |
| `rail` | Char |  |
| `description` | Text |  |
| `instructions` | Text |  |
| `icon` | Char |  |
| `is_enabled` | Boolean |  |
| `supports_deposit` | Boolean |  |
| `supports_withdrawal` | Boolean |  |
| `requires_approval` | Boolean |  |
| `min_amount` | Decimal |  |
| `max_amount` | Decimal |  |
| `position` | Integer |  |
| `config` | JSON |  |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |

#### `Wallet`

One account's money: its currency, whether it may move, and nothing else.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `user` | OneToOne | unique, → `accounts.User` |
| `currency` | Char |  |
| `status` | Char |  |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |

#### `WalletCharge`

One fee, as it was charged, kept beside the movement it came off.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `entry` | ForeignKey | → `wallet.WalletEntry` |
| `kind` | Char |  |
| `label` | Char |  |
| `percent` | Decimal |  |
| `amount` | Decimal |  |
| `absorbed` | Boolean |  |
| `fee` | ForeignKey | → `wallet.MethodFee`, nullable |
| `position` | Integer |  |
| `created_at` | DateTime | not editable |

#### `WalletCheckpoint`

A run of archived entries, collapsed into the balance they left behind.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `wallet` | ForeignKey | → `wallet.Wallet` |
| `sequence` | PositiveInteger |  |
| `balance` | Decimal |  |
| `credited` | Decimal |  |
| `debited` | Decimal |  |
| `entry_count` | PositiveInteger |  |
| `created_at` | DateTime | not editable |

#### `WalletEntry`

One movement of money, and where it has got to.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `wallet` | ForeignKey | → `wallet.Wallet` |
| `kind` | Char |  |
| `direction` | Char | not editable |
| `method` | Char |  |
| `payment_method` | ForeignKey | → `wallet.PaymentMethod`, nullable |
| `network` | ForeignKey | → `wallet.MethodNetwork`, nullable |
| `amount` | Decimal |  |
| `currency` | Char |  |
| `gross_amount` | Decimal |  |
| `fee_total` | Decimal |  |
| `exchange_rate` | Decimal | nullable |
| `destination` | Char |  |
| `approval` | Char |  |
| `reviewed_by` | ForeignKey | → `accounts.User`, nullable |
| `reviewed_at` | DateTime | nullable |
| `review_note` | Char |  |
| `status` | Char |  |
| `reference` | Char |  |
| `external_reference` | Char |  |
| `description` | Char |  |
| `metadata` | JSON |  |
| `counterparty` | ForeignKey | → `wallet.WalletEntry`, nullable |
| `checkpoint` | ForeignKey | → `wallet.WalletCheckpoint`, nullable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `settled_at` | DateTime | nullable |
<!-- /generated:models -->

## Admin

Amounts are stored at four decimals, so that a rail quoting minor units has
somewhere to put them, and they are **written** at the precision they actually
carry: `1250.50 USD` rather than `1250.5000 USD`, and `12.3456 USD` where all
four digits are real. Never rounded for display -- a total that disagrees with
the rows above it is worse than a wide column.

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `ExchangeRate` | Yes | — | `pair_display`, `rate`, `margin_percent`, `source`, `effective_from`, `is_enabled` |
| `MethodCurrency` | Yes | — | `currency`, `method`, `bounds_display`, `network_count`, `is_enabled` |
| `MethodFee` | Yes | — | `method`, `kind`, `label`, `applies_to`, `cost_display`, `absorbed`, `is_enabled` |
| `PaymentMethod` | Yes | `enable_methods`, `disable_methods` | `name`, `code`, `rail`, `directions_display`, `currencies_display`, `fee_display`, `approval_display`, `is_enabled` |
| `Wallet` | Yes | — | `user`, `currency`, `status`, `settled_display`, `available_display`, `created_at` |
| `WalletCheckpoint` | Yes | — | `wallet`, `sequence`, `balance`, `credited`, `debited`, `entry_count`, `created_at` |
| `WalletEntry` | Yes | `approve_entries`, `reject_entries`, `settle_entries`, `fail_entries` | `created_at`, `wallet`, `kind`, `method_display`, `amount_display`, `cost_display`, `status`, `approval_display` |
<!-- /generated:admin -->

## Settings

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_WALLET_ENABLED` | **Yes** | whether this deployment carries wallets at all -- their tables, their routes and their admin. |
| `DJANGO_WALLET_CURRENCY` | **Yes** | the ISO 4217 code every amount in every wallet is held in. |
| `DJANGO_WALLET_METHODS` | Recommended | which rails this deployment can actually move money on. |
| `DJANGO_WALLET_ARCHIVE_AFTER_DAYS` | Optional | how old a settled entry has to be before `manage.py wallet_archive` folds it into a checkpoint. Range 1–365. |
| `DJANGO_WALLET_ARCHIVE_THRESHOLD` | Optional | how many unarchived entries are enough to fold early, without waiting for the age. Range 1–10000. |
| `DJANGO_WALLET_AUTO_CREATE` | Optional | whether an account's first wallet request opens one for it. |
| `DJANGO_WALLET_WEBHOOK_SECRETS` | Optional | the secret each payment rail signs its confirmations with, as `method-code:secret` pairs. |
| `DJANGO_WALLET_WEBHOOK_TOLERANCE_SECONDS` | Optional | how far out of date a signed rail confirmation may be. Range 30–3600. |
| `DJANGO_WALLET_OVERDRAFT_LIMIT` | Optional | how far below zero a wallet may be taken, for a deployment that runs credit. 0 or more. |
| `DJANGO_WALLET_MIN_DEPOSIT` | Optional | the smallest deposit worth the rail's fee, or zero for no floor. 0 or more. |
| `DJANGO_WALLET_MAX_DEPOSIT` | Optional | the largest single deposit accepted, or zero for no ceiling. 0 or more. |
| `DJANGO_WALLET_MIN_WITHDRAWAL` | Optional | the smallest payout worth making, or zero for no floor. 0 or more. |
| `DJANGO_WALLET_MAX_WITHDRAWAL` | Optional | the largest single payout allowed without a human, or zero for no ceiling. 0 or more. |
| `DJANGO_WALLET_PAGE_SIZE` | Optional | how many entries a listing returns when the caller does not say. Range 1–200. |
| `DJANGO_WALLET_MAX_PAGE_SIZE` | Optional | the ceiling on `limit`, so one request cannot ask for a whole history. Range 1–1000. |
<!-- /generated:settings -->
