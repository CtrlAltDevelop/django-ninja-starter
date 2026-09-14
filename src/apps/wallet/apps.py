from django.apps import AppConfig
from django.conf import settings

from infrastructure.common.appsettings import AppSettings, Requirement, Rule


def unknown_rails() -> list[str]:
    """The names in ``DJANGO_WALLET_METHODS`` that are not a rail this app knows.

    A rule rather than ``choices``, because the setting is a list and ``choices``
    compares one value. Imported at check time, not at module level, so reading
    the app config never drags the rail catalogue in with it.
    """
    from apps.wallet.methods import Method

    return sorted(set(settings.WALLET_METHODS) - set(Method.values))


class WalletConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.wallet"
    label = "wallet"
    verbose_name = "Wallet"

    settings_spec = AppSettings(
        title="Wallet",
        summary=(
            "A wallet per account, every way money gets in and out, and a balance "
            "derived from the entries rather than stored beside them."
        ),
        requirements=(
            Requirement(
                "WALLET_ENABLED",
                env="DJANGO_WALLET_ENABLED",
                purpose=(
                    "whether this deployment carries wallets at all -- their tables, "
                    "their routes and their admin"
                ),
                required=True,
                hint=(
                    "The app is installed, so something put it in INSTALLED_APPS while "
                    "DJANGO_WALLET_ENABLED was off. Set it to true, or drop the app: a "
                    "half enabled wallet migrates its tables and publishes none of its "
                    "routes."
                ),
            ),
            Requirement(
                "WALLET_CURRENCY",
                env="DJANGO_WALLET_CURRENCY",
                purpose="the ISO 4217 code every amount in every wallet is held in",
                required=True,
                pattern=r"[A-Z]{3}",
                pattern_description="be a three-letter ISO 4217 code, such as USD",
                hint=(
                    "Amounts are stored as bare decimals, so this is the only record of "
                    "what they mean. Changing it on a deployment that already holds "
                    "money redenominates every balance in it."
                ),
            ),
            Requirement(
                "WALLET_METHODS",
                env="DJANGO_WALLET_METHODS",
                purpose="which rails this deployment can actually move money on",
                recommended=True,
                hint=(
                    "Empty means every rail the app knows, including ones nothing here "
                    "has integrated -- so the API advertises a payout by cheque that no "
                    "process will ever pay. Name the ones you have."
                ),
            ),
            Requirement(
                "WALLET_ARCHIVE_AFTER_DAYS",
                env="DJANGO_WALLET_ARCHIVE_AFTER_DAYS",
                purpose=(
                    "how old a settled entry has to be before `manage.py wallet_archive` "
                    "folds it into a checkpoint"
                ),
                minimum=1,
                maximum=365,
            ),
            Requirement(
                "WALLET_ARCHIVE_THRESHOLD",
                env="DJANGO_WALLET_ARCHIVE_THRESHOLD",
                purpose=(
                    "how many unarchived entries are enough to fold early, without "
                    "waiting for the age"
                ),
                minimum=1,
                maximum=10_000,
                hint=(
                    "Every unarchived entry is re-summed on every balance read. A high "
                    "threshold makes reads slower; a very low one cuts a checkpoint row "
                    "per movement."
                ),
            ),
            Requirement(
                "WALLET_AUTO_CREATE",
                env="DJANGO_WALLET_AUTO_CREATE",
                purpose="whether an account's first wallet request opens one for it",
            ),
            Requirement(
                "WALLET_WEBHOOK_SECRETS",
                env="DJANGO_WALLET_WEBHOOK_SECRETS",
                purpose=(
                    "the secret each payment rail signs its confirmations with, as "
                    "`method-code:secret` pairs"
                ),
                hint=(
                    "Settling a movement is where money becomes real, so it is the "
                    "rail's statement and never the account's. A method with no "
                    "secret here has no way to confirm anything: its movements stay "
                    "pending until an operator applies them, which is the safe "
                    "direction to fail but will look like a stuck queue if you meant "
                    "to wire the webhook up."
                ),
            ),
            Requirement(
                "WALLET_WEBHOOK_TOLERANCE_SECONDS",
                env="DJANGO_WALLET_WEBHOOK_TOLERANCE_SECONDS",
                purpose="how far out of date a signed rail confirmation may be",
                minimum=30,
                maximum=3600,
                hint=(
                    "The signature covers the timestamp, so this is what stops a "
                    "confirmation captured off the wire being replayed later. Long "
                    "enough for a slow rail and a clock a little out; no longer."
                ),
            ),
            Requirement(
                "WALLET_OVERDRAFT_LIMIT",
                env="DJANGO_WALLET_OVERDRAFT_LIMIT",
                purpose=(
                    "how far below zero a wallet may be taken, for a deployment that runs credit"
                ),
                minimum=0,
                hint=(
                    "Zero -- the default -- means a wallet can never go negative. "
                    "Anything else is credit you are extending, and this app does not "
                    "collect it."
                ),
            ),
            Requirement(
                "WALLET_MIN_DEPOSIT",
                env="DJANGO_WALLET_MIN_DEPOSIT",
                purpose="the smallest deposit worth the rail's fee, or zero for no floor",
                minimum=0,
            ),
            Requirement(
                "WALLET_MAX_DEPOSIT",
                env="DJANGO_WALLET_MAX_DEPOSIT",
                purpose="the largest single deposit accepted, or zero for no ceiling",
                minimum=0,
            ),
            Requirement(
                "WALLET_MIN_WITHDRAWAL",
                env="DJANGO_WALLET_MIN_WITHDRAWAL",
                purpose="the smallest payout worth making, or zero for no floor",
                minimum=0,
            ),
            Requirement(
                "WALLET_MAX_WITHDRAWAL",
                env="DJANGO_WALLET_MAX_WITHDRAWAL",
                purpose="the largest single payout allowed without a human, or zero for no ceiling",
                minimum=0,
                hint=(
                    "Zero means this app will pay out any amount the balance covers. A "
                    "ceiling is the cheapest defence against a compromised account "
                    "emptying a wallet in one call."
                ),
            ),
            Requirement(
                "WALLET_PAGE_SIZE",
                env="DJANGO_WALLET_PAGE_SIZE",
                purpose="how many entries a listing returns when the caller does not say",
                minimum=1,
                maximum=200,
            ),
            Requirement(
                "WALLET_MAX_PAGE_SIZE",
                env="DJANGO_WALLET_MAX_PAGE_SIZE",
                purpose="the ceiling on `limit`, so one request cannot ask for a whole history",
                minimum=1,
                maximum=1_000,
            ),
        ),
        rules=(
            Rule(
                holds=lambda: not unknown_rails(),
                message=(
                    "WALLET_METHODS names a rail this app does not know, so nothing can "
                    "ever be paid on it -- and a list with only misspellings in it "
                    "switches every rail off"
                ),
                settings=("WALLET_METHODS",),
                hint=(
                    "Use the rail names from apps.wallet.methods.Method, such as "
                    "card, bank_transfer, crypto or cash. A payment method's own code "
                    "belongs in DJANGO_WALLET_WEBHOOK_SECRETS, not here."
                ),
            ),
            Rule(
                holds=lambda: settings.WALLET_MAX_PAGE_SIZE >= settings.WALLET_PAGE_SIZE,
                message=(
                    "WALLET_MAX_PAGE_SIZE is below WALLET_PAGE_SIZE, so the ceiling "
                    "clamps the default and no listing can return a full page"
                ),
                settings=("WALLET_MAX_PAGE_SIZE", "WALLET_PAGE_SIZE"),
                hint="Raise DJANGO_WALLET_MAX_PAGE_SIZE, or lower DJANGO_WALLET_PAGE_SIZE.",
            ),
            Rule(
                holds=lambda: (
                    not settings.WALLET_MAX_DEPOSIT
                    or settings.WALLET_MAX_DEPOSIT >= settings.WALLET_MIN_DEPOSIT
                ),
                message=(
                    "WALLET_MAX_DEPOSIT is below WALLET_MIN_DEPOSIT, so every deposit is "
                    "refused by one limit or the other"
                ),
                settings=("WALLET_MAX_DEPOSIT", "WALLET_MIN_DEPOSIT"),
                hint="Raise DJANGO_WALLET_MAX_DEPOSIT, or lower DJANGO_WALLET_MIN_DEPOSIT.",
            ),
            Rule(
                holds=lambda: (
                    not settings.WALLET_MAX_WITHDRAWAL
                    or settings.WALLET_MAX_WITHDRAWAL >= settings.WALLET_MIN_WITHDRAWAL
                ),
                message=(
                    "WALLET_MAX_WITHDRAWAL is below WALLET_MIN_WITHDRAWAL, so no payout "
                    "can satisfy both limits"
                ),
                settings=("WALLET_MAX_WITHDRAWAL", "WALLET_MIN_WITHDRAWAL"),
                hint=("Raise DJANGO_WALLET_MAX_WITHDRAWAL, or lower DJANGO_WALLET_MIN_WITHDRAWAL."),
            ),
        ),
    )
