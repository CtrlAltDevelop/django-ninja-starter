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
   WALLET_CURRENCY = "USD"  # the only record of what the amounts mean
   WALLET_METHODS = ()  # rails this deployment runs; empty means all
   WALLET_AUTO_CREATE = True
   WALLET_ARCHIVE_AFTER_DAYS = 1
   WALLET_ARCHIVE_THRESHOLD = 20
   WALLET_OVERDRAFT_LIMIT = Decimal("0")
   WALLET_MAX_WITHDRAWAL = Decimal("0")  # 0 means no ceiling
   ```

3. Mount the router wherever your Django Ninja API is built:

   ```python
   api.add_router("/wallet", "apps.wallet.rest.v1.router")
   ```

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

**Every write that depends on a balance takes the wallet's row lock first**, and
every write takes a `reference` that is unique per wallet, so a retried request
returns the first entry rather than moving the money twice. That lock needs a
database that has one: `select_for_update` compiles to nothing on SQLite, so run
this on PostgreSQL or MySQL before there is real money in it.

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
| `admin.py` | The configuration screens, and the approval queue |
| `adminui.py` | The sidebar group, and the three numbers on the dashboard |
| `rest/`, `graph/`, `grpc/` | The three doors. None of them decides anything |

## Commands

```bash
manage.py wallet_methods    # seed switched-off methods, once
manage.py wallet_archive    # fold settled entries into checkpoints, daily
```
