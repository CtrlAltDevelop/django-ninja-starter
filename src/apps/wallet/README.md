# `apps.wallet`

A wallet per account, every way money gets in and out, and a balance derived
from the movements rather than stored beside them.

This directory holds the whole feature — models, the configuration catalogue, the
pricing engine, services, the HTTP transport, admin, migrations, commands and
tests — and no other feature app reaches into it. What it needs from outside is
one package and two optional ones:

| What | Needed | If it is missing |
| --- | --- | --- |
| `infrastructure/common` | **Required** | The app will not import at all. It supplies the settings contract in `apps.py`. Copy this directory alongside `apps/wallet`. |
| `infrastructure.auth.core.sessions` | Optional | Guarded by `try/except ImportError`. Without it the router falls back to Django's own `django_auth`, so the app still works — it just stops accepting bearer tokens. |
| `unfold` | Optional | Only the admin theme. `theme.py` resolves Django's own admin behind it. |

So "copy the directory" means copying **two**: `apps/wallet` and
`infrastructure/common`. There is no third. The app reads no project setting it
does not default for itself, and imports no other feature app at all.

That is checked rather than claimed. Enabling only `DJANGO_WALLET_ENABLED`
leaves a project holding exactly three of its own apps — `accounts`, `common`
and `wallet` — with no login app installed, so the guard above is genuinely
exercised. `tests/test_app_isolation.py` then drives the whole round trip in
that configuration over Django's own session: configure a method, quote a
deposit, make it, settle it, and check the balance matches the quote. Booting is
not the claim; with a wallet the gap between "it installed" and "it works" is
somebody's money.

## Dropping it into another Django project

1. Copy this directory to `apps/wallet` (or anywhere importable) and add
   `apps.wallet.apps.WalletConfig` to `INSTALLED_APPS`.
2. Add the settings it reads. Every one has a default except the currency:

   ```python
   WALLET_ENABLED = True
   WALLET_CURRENCY = "USD"  # the only record of what the amounts mean
   WALLET_METHODS = ()  # rails this deployment runs; empty means all
   WALLET_AUTO_CREATE = True
   # One secret per method code. A method with none confirms nothing, and its
   # movements wait for an operator.
   WALLET_WEBHOOK_SECRETS = {"card": "whsec_..."}
   WALLET_WEBHOOK_TOLERANCE_SECONDS = 300
   WALLET_ARCHIVE_AFTER_DAYS = 1
   WALLET_ARCHIVE_THRESHOLD = 20
   WALLET_OVERDRAFT_LIMIT = Decimal("0")
   # The deployment's own bounds. 0 means no bound.
   WALLET_MIN_DEPOSIT = Decimal("0")
   WALLET_MAX_DEPOSIT = Decimal("0")
   WALLET_MIN_WITHDRAWAL = Decimal("0")
   WALLET_MAX_WITHDRAWAL = Decimal("0")
   WALLET_PAGE_SIZE = 50
   WALLET_MAX_PAGE_SIZE = 200
   ```

3. Mount the router wherever your Django Ninja API is built:

   ```python
   api.add_router("/wallet", "apps.wallet.rest.v1.router")
   ```

   That one mount publishes the rail's signed webhook too, at
   `/wallet/hooks/{method}`: it is a sub-router of this one, authenticated by
   signature rather than by account. Point each processor's webhook there.

4. `manage.py migrate`, then `manage.py wallet_methods` to create a starting set
   of payment methods — switched off, charging nothing — for an administrator to
   finish filling in.
5. Schedule `manage.py wallet_archive` daily. Nothing breaks without it; reads
   just get slower forever.

## What it does

**There is no balance column.** A stored balance is a second copy of a fact the
movements already hold, and the two disagree the first time a process dies
between writing a movement and updating the column. So the balance is derived:
the last checkpoint, plus the entries written since it. `wallet_archive` folds
settled entries into checkpoints so that second number stays small.

**A caller is never given one number.** `settled` is what the wallet holds,
`available` is what may be spent — `settled` less what pending payouts have
already claimed — and `projected` is where it lands if everything outstanding
succeeds. Authorise against `available`.

**A pending entry is not money.** A card deposit is pending until the processor
confirms it, and a pending entry counted as balance is money the wallet does not
have.

**Approval is a second, independent axis.** `status` is what the money is doing;
`approval` is what a person decided. A movement through a method configured to
require approval is a *request* until an operator applies it in the admin, and
cannot settle before they do. New methods require approval by default.

**Ways to pay are rows, not code.** `methods.py` declares what a card or a chain
*is* — reviewed in code, not editable in a form. `catalog.py` holds what an
administrator configures: which processor, which currencies, which chains, what
it charges, what it converts at. `GET /wallet/methods` is the only place a client
can learn any of it.

**The same function quotes and charges.** `charges.py` prices a movement, and
both `POST /wallet/quotes` and the recording path call it. An amount quoted is
the amount charged.

**Transfers between two wallets here are free, and cannot be made otherwise.**
The money never leaves, so there is nothing to pass on; a fee saved against the
internal rail is refused at the point somebody tries to store it.

**Confirming a movement is never the account's own.** `settle`, `fail`, `expire`
and `reverse` are published on no transport. A rail confirms its own movements at
`POST /wallet/hooks/{method}`, signed; an operator does the same from the admin.
The account keeps `cancel`, because giving up on a payment you started asserts
nothing about the outside world.

**A movement nothing confirms does not wait forever.** Set
`DJANGO_WALLET_EXPIRE_AFTER_HOURS` and run `wallet_expire`: a pending movement
older than that is expired and the money it had claimed goes back into
`available`. A movement waiting on a *person* is never expired by the job — that
queue is cleared by clearing it. Left at `0`, nothing expires.

**Corrections are written by the service, not typed into a row.** A bonus, a fee
or an adjustment goes through `WalletService.adjust`, and the one add form in the
app — Adjustments, in the admin — calls it, so a hand-written correction still
takes the lock, is still written once per reference, and is still refused when
the money is not there. An adjustment without a reason is refused too.

**A wallet's status is changed by a service call, not an edit.** Frozen refuses
payouts and still takes money in, which is what a compliance hold means. Closed
is refused while a settled balance or a pending movement would be stranded by it,
and stays closed once set.

**Every hand-made change records who made it.** `reviewed_by` for an approval,
`settled_by_operator` / `failed_by_operator` / `expired_by_operator` in the
entry's metadata for the admin actions, `reversed_by_operator` for a reversal,
`adjusted_by` for a correction. Settling is where a row becomes money, so who
said so is part of the record. A transition with no operator on it came from a
rail's signed confirmation, from the account cancelling its own movement, or from
the expiry job.

**`metadata` is the caller's field, so it has a ceiling.**
`DJANGO_WALLET_MAX_METADATA_BYTES`, 4096 by default, `0` to turn it off. It is
returned on every read of that entry, so uncapped it is a way for one account to
make everybody's history expensive.

**It says what it did.** `signals.py` sends `entry_recorded`, `entry_settled`,
`entry_failed`, `entry_reversed`, `payout_ready` and `wallet_status_changed`
after commit, so notifications or a payout integration can listen without this
app importing either.

**Every write that depends on a balance takes the wallet's row lock first**, and
every write takes a `reference` that is unique per wallet, so a retried request
returns the first entry rather than moving the money twice. That lock needs a
database that has one: `select_for_update` compiles to nothing on SQLite, so run
this on PostgreSQL or MySQL before there is real money in it.

## Topping up through a hosted gateway

Zarinpal, IDPay, Zibal, NextPay, Pay.ir, Vandar, Saman (SEP), Mellat
(Behpardakht), Sadad (Bank Melli), Stripe and PayPal live in `gateways.py`. You
configure them the way sign-in providers are configured: fill a gateway's keys in
the environment (`ZARINPAL_MERCHANT_ID`, `STRIPE_SECRET_KEY`, ...; see
`.env.example`) and it is offered. Leave them empty and it does not exist.

1. Set the keys, and `DJANGO_WALLET_GATEWAY_CALLBACK_URL` to the wallet's public
   prefix (for example `https://api.example.com/api/v1/wallet`).
2. In the admin, add a payment method whose **code is the gateway's key**
   (`zarinpal`), rail `gateway`, and turn deposits on. Fees, limits and approval
   on that method apply as usual. The Iranian gateways take rials, so price them
   in `IRR`.
3. `GET /wallet/gateways` lists what is offered. `POST
   /wallet/gateways/{code}/deposits` records a `pending` deposit and returns
   `redirect`. Send the customer there: a link for `GET`, an auto-submitted form of
   `fields` for `POST` (Mellat).
4. The gateway sends the customer back to
   `/wallet/hooks/gateways/{code}/{entry_id}`. Nothing on that trip is believed.
   It must name the token the gateway gave us, and then this server asks the
   gateway, with the merchant credential, whether that amount was paid. Only that
   answer settles the deposit. If the gateway can't be reached, the deposit stays
   `pending`. `DJANGO_WALLET_GATEWAY_RETURN_URL` is where the customer lands
   afterwards, with `?entry=&status=`.

`DJANGO_WALLET_GATEWAY_SANDBOX=true` points every gateway that has a sandbox at
it.

## The files

| File | What is in it |
| --- | --- |
| `money.py` | Field widths for amounts, percentages and rates, and the rounding |
| `methods.py` | The rail *types*: what a card, a chain, a cash payment physically is |
| `catalog.py` | What an administrator configures: methods, currencies, chains, fees, rates |
| `charges.py` | Pricing: fees, limits, conversion. No writes, no locks |
| `models.py` | The ledger: wallets, entries, checkpoints, charges |
| `balances.py` | Reading a balance, and folding entries into checkpoints |
| `errors.py` | Every refusal, and the status each carries |
| `services.py` | Everything an account can do, decided once for every transport |
| `signals.py` | What the wallet announces, sent after commit |
| `admin.py` | The configuration screens, and the approval queue |
| `adminui.py` | The sidebar group, and the three numbers on the dashboard |
| `rest/`, `graph/`, `grpc/` | The three doors. None of them decides anything |

## Commands

```bash
manage.py wallet_methods    # seed switched-off methods, once
manage.py wallet_archive    # fold settled entries into checkpoints, daily
manage.py wallet_expire     # give up on movements nothing confirmed, hourly
```
