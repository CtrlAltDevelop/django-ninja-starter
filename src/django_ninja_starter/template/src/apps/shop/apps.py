from django.apps import AppConfig


class ShopConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.shop"
    verbose_name = "Shop"

    # The settings this app reads, as plain rows rather than as a class imported
    # from the project: documenting them must not be the thing that stops the
    # directory being copied somewhere else.
    settings_docs = (
        (
            "DJANGO_SHOP_ENABLED",
            "**Yes**",
            "Installs the app, its migrations, its routes and its admin. Unset, "
            "a project carries no shop at all",
        ),
        (
            "DJANGO_SHOP_CURRENCY",
            "Optional",
            "The ISO 4217 code every price is quoted in. Defaults to `USD`",
        ),
        (
            "DJANGO_SHOP_REVIEW_MODERATION",
            "Optional",
            "Whether a review waits for a moderator before anybody can read it. Defaults to on",
        ),
        (
            "DJANGO_SHOP_MAX_ITEM_QUANTITY",
            "Optional",
            "The most of one product a single cart line may hold. Defaults to 99",
        ),
        (
            "DJANGO_SHOP_PAGE_SIZE",
            "Optional",
            "How many products a listing returns when the caller does not say. Defaults to 24",
        ),
        (
            "DJANGO_SHOP_MAX_PAGE_SIZE",
            "Optional",
            "The ceiling on `limit`, so one request cannot ask for the catalogue. Defaults to 100",
        ),
    )
