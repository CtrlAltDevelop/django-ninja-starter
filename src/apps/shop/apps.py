from django.apps import AppConfig
from django.conf import settings

from infrastructure.common.appsettings import AppSettings, Requirement, Rule


class ShopConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.shop"
    label = "shop"
    verbose_name = "Shop"

    settings_spec = AppSettings(
        title="Shop",
        summary="A catalogue, carts, orders and moderated reviews, over REST, GraphQL and gRPC.",
        requirements=(
            Requirement(
                "SHOP_ENABLED",
                env="DJANGO_SHOP_ENABLED",
                purpose=(
                    "whether this deployment carries the shop at all -- its tables, its "
                    "routes and its admin"
                ),
                required=True,
                hint=(
                    "The app is installed, so something put it in INSTALLED_APPS while "
                    "DJANGO_SHOP_ENABLED was off. Set it to true, or drop the app: a half "
                    "enabled shop migrates its tables and publishes none of its routes."
                ),
            ),
            Requirement(
                "SHOP_CURRENCY",
                env="DJANGO_SHOP_CURRENCY",
                purpose="the ISO 4217 code every price in the catalogue is quoted in",
                required=True,
                pattern=r"[A-Z]{3}",
                pattern_description="be a three-letter ISO 4217 code, such as USD",
                hint=(
                    "Three letters, such as USD or EUR. Prices are stored as bare "
                    "decimals, so this is the only record of what they mean -- changing "
                    "it on a shop that already has orders reprices history."
                ),
            ),
            Requirement(
                "SHOP_REVIEW_MODERATION",
                env="DJANGO_SHOP_REVIEW_MODERATION",
                purpose="whether a review waits for a moderator before anybody can read it",
                recommended=True,
                hint=(
                    "Turning it off publishes whatever a stranger types straight onto a "
                    "product page. Leave it on unless something else moderates."
                ),
            ),
            Requirement(
                "SHOP_MAX_ITEM_QUANTITY",
                env="DJANGO_SHOP_MAX_ITEM_QUANTITY",
                purpose="the most of one product a single cart line may hold",
                minimum=1,
                maximum=10_000,
            ),
            Requirement(
                "SHOP_PAGE_SIZE",
                env="DJANGO_SHOP_PAGE_SIZE",
                purpose="how many products a listing returns when the caller does not say",
                minimum=1,
                maximum=200,
            ),
            Requirement(
                "SHOP_MAX_PAGE_SIZE",
                env="DJANGO_SHOP_MAX_PAGE_SIZE",
                purpose="the ceiling on `limit`, so one request cannot ask for the catalogue",
                minimum=1,
                maximum=1_000,
                hint=(
                    "It has to be at least DJANGO_SHOP_PAGE_SIZE: a ceiling below the "
                    "default would clamp every request that named no limit at all."
                ),
            ),
        ),
        rules=(
            Rule(
                holds=lambda: settings.SHOP_MAX_PAGE_SIZE >= settings.SHOP_PAGE_SIZE,
                message=(
                    "SHOP_MAX_PAGE_SIZE is below SHOP_PAGE_SIZE, so the ceiling clamps "
                    "the default and no listing can return a full page"
                ),
                settings=("SHOP_MAX_PAGE_SIZE", "SHOP_PAGE_SIZE"),
                hint="Raise DJANGO_SHOP_MAX_PAGE_SIZE, or lower DJANGO_SHOP_PAGE_SIZE.",
            ),
        ),
    )
