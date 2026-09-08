"""What the admin looks like: its navigation, its badge, and its front page.

Unfold themes the admin, and everything it themes is configured through one
``UNFOLD`` dictionary. The parts of that dictionary which have to *think* live
here rather than in settings, for two reasons.

**The navigation has to match what is installed.** Every authentication app in
this project is optional, so a sidebar written as a fixed list would either link
to admin pages that do not exist -- ``NoReverseMatch``, on the first page load,
in production -- or quietly omit apps that are switched on. So the sidebar is
built from the app labels that actually registered, and a deployment running
only password login sees exactly the sections it has.

**A link nobody may follow is worse than no link.** Every item carries a
permission check, so an editor with `cms.change_field` sees Content and nothing
else, rather than a menu of pages that answer 403.

The dashboard is the same idea applied to numbers: it counts what is there, and
says nothing about apps that are not installed.
"""

from datetime import timedelta
from typing import Any

from django.apps import apps
from django.db.models import Case, Count, F, Q, Sum, When
from django.http import HttpRequest
from django.urls import reverse_lazy
from django.utils import timezone

from infrastructure.common.app_labels import app_installed


def environment_badge(request: HttpRequest) -> list[str] | None:
    """The label beside the account menu, so nobody edits production by mistake.

    Debug is the signal rather than a setting of its own: it is already the line
    between a machine somebody is developing on and one real users can reach.
    """
    from django.conf import settings

    if settings.DEBUG:
        return ["Development", "warning"]
    return None


def _changelist(app_label: str, model: str) -> Any:
    return reverse_lazy(f"admin:{app_label}_{model}_changelist")


def _may(*permissions: str) -> Any:
    """A permission check for one navigation item.

    Superusers pass everything, which is Django's own rule; anybody else needs
    one of the named permissions.
    """

    def check(request: HttpRequest) -> bool:
        user = request.user
        return bool(user.is_superuser or any(user.has_perm(name) for name in permissions))

    return check


def _content_group() -> dict[str, Any]:
    return {
        "title": "Content",
        "separator": False,
        "collapsible": False,
        "items": [
            {
                "title": "Pages",
                "icon": "web",
                "link": _changelist("cms", "page"),
                "permission": _may("cms.view_page", "cms.change_field"),
            },
            {
                "title": "Sections",
                "icon": "view_agenda",
                "link": _changelist("cms", "section"),
                "permission": _may("cms.view_section"),
            },
            {
                "title": "Shared sections",
                "icon": "content_copy",
                "link": _changelist("cms", "sectionplacement"),
                "permission": _may("cms.view_sectionplacement"),
            },
            {
                "title": "Fields",
                "icon": "text_fields",
                "link": _changelist("cms", "field"),
                "permission": _may("cms.view_field"),
            },
            {
                "title": "Menus",
                "icon": "menu",
                "link": _changelist("cms", "menu"),
                "permission": _may("cms.view_menu"),
            },
            {
                "title": "Site events",
                "icon": "event",
                "link": _changelist("cms", "siteevent"),
                "permission": _may("cms.view_siteevent", "cms.change_siteevent"),
            },
            {
                "title": "Site settings",
                "icon": "public",
                "link": _changelist("cms", "sitesettings"),
                "permission": _may("cms.view_sitesettings", "cms.change_sitesettings"),
            },
        ],
    }


def _notifications_group() -> dict[str, Any]:
    """Writing an announcement, and finding out who has read one."""
    return {
        "title": "Notifications",
        "separator": False,
        "collapsible": False,
        "items": [
            {
                "title": "Notifications",
                "icon": "notifications",
                "link": _changelist("notifications", "notification"),
                "permission": _may("notifications.view_notification"),
            },
            {
                "title": "Read receipts",
                "icon": "mark_email_read",
                "link": _changelist("notifications", "notificationreceipt"),
                "permission": _may("notifications.view_notificationreceipt"),
            },
        ],
    }


def _shop_group() -> dict[str, Any]:
    """The catalogue somebody curates, and the orders that come out of it.

    Ordered the way a shopkeeper's day is rather than the way the models are:
    what needs doing (orders, payments, moderation) sits above what is designed
    once and edited rarely (the category tree, the attribute schema). The
    records nobody edits are collapsed at the bottom, because they are read
    after something went wrong rather than as part of the work.
    """
    return {
        "title": "Shop",
        "separator": False,
        "collapsible": False,
        "items": [
            {
                "title": "Orders",
                "icon": "receipt_long",
                "link": _changelist("shop", "order"),
                "permission": _may("shop.view_order"),
            },
            {
                "title": "Payments",
                "icon": "payments",
                "link": _changelist("shop", "payment"),
                "permission": _may("shop.view_payment"),
            },
            {
                "title": "Invoices",
                "icon": "description",
                "link": _changelist("shop", "invoice"),
                "permission": _may("shop.view_invoice"),
            },
            {
                "title": "Products",
                "icon": "inventory_2",
                "link": _changelist("shop", "product"),
                "permission": _may("shop.view_product", "shop.change_product"),
            },
            {
                "title": "Categories",
                "icon": "account_tree",
                "link": _changelist("shop", "category"),
                "permission": _may("shop.view_category"),
            },
            {
                "title": "Brands",
                "icon": "sell",
                "link": _changelist("shop", "brand"),
                "permission": _may("shop.view_brand"),
            },
            {
                "title": "Sellers",
                "icon": "storefront",
                "link": _changelist("shop", "seller"),
                "permission": _may("shop.view_seller"),
            },
            {
                "title": "Collections",
                "icon": "collections_bookmark",
                "link": _changelist("shop", "collection"),
                "permission": _may("shop.view_collection"),
            },
            {
                "title": "Discounts",
                "icon": "local_offer",
                "link": _changelist("shop", "discount"),
                "permission": _may("shop.view_discount"),
            },
            {
                "title": "Coupons",
                "icon": "confirmation_number",
                "link": _changelist("shop", "coupon"),
                "permission": _may("shop.view_coupon"),
            },
            {
                "title": "Delivery options",
                "icon": "local_shipping",
                "link": _changelist("shop", "shippingmethod"),
                "permission": _may("shop.view_shippingmethod"),
            },
            {
                "title": "Reviews",
                "icon": "reviews",
                "link": _changelist("shop", "review"),
                "permission": _may("shop.view_review", "shop.change_review"),
            },
            {
                "title": "Stock held",
                "icon": "inventory",
                "link": _changelist("shop", "inventoryreservation"),
                "permission": _may("shop.view_inventoryreservation"),
            },
            {
                "title": "Baskets",
                "icon": "shopping_cart",
                "link": _changelist("shop", "cart"),
                "permission": _may("shop.view_cart"),
            },
        ],
    }


def _people_group() -> dict[str, Any]:
    items = [
        {
            "title": "Accounts",
            "icon": "person",
            "link": _changelist("accounts", "user"),
            "permission": _may("accounts.view_user"),
        },
        {
            "title": "Profiles",
            "icon": "badge",
            "link": _changelist("accounts", "profile"),
            "permission": _may("accounts.view_profile"),
        },
        {
            "title": "Groups",
            "icon": "groups",
            "link": _changelist("auth", "group"),
            "permission": _may("auth.view_group"),
        },
    ]
    if app_installed("auth_core"):
        items.append(
            {
                "title": "Phone numbers",
                "icon": "smartphone",
                "link": _changelist("auth_core", "phonenumber"),
                "permission": _may("auth_core.view_phonenumber"),
            }
        )
    if app_installed("auth_twofactor"):
        items.append(
            {
                "title": "Second factors",
                "icon": "encrypted",
                "link": _changelist("auth_twofactor", "secondfactor"),
                "permission": _may("auth_twofactor.view_secondfactor"),
            }
        )
    return {"title": "People", "separator": True, "collapsible": False, "items": items}


def _credentials_group() -> dict[str, Any] | None:
    """Whatever the active token mode owns, and nothing from the other two."""
    items = []
    if app_installed("oauth_rotation"):
        items.append(
            {
                "title": "Token families",
                "icon": "key",
                "link": _changelist("oauth_rotation", "tokenfamily"),
                "permission": _may("oauth_rotation.view_tokenfamily"),
            }
        )
    if app_installed("oauth_session"):
        items.append(
            {
                "title": "Sessions",
                "icon": "devices",
                "link": _changelist("oauth_session", "oauthsession"),
                "permission": _may("oauth_session.view_oauthsession"),
            }
        )
    if app_installed("oauth_sliding"):
        items.append(
            {
                "title": "Sliding tokens",
                "icon": "timer",
                "link": _changelist("oauth_sliding", "slidingtoken"),
                "permission": _may("oauth_sliding.view_slidingtoken"),
            }
        )
    if app_installed("oauth_core"):
        items.append(
            {
                "title": "OAuth clients",
                "icon": "apps",
                "link": _changelist("oauth_core", "oauthclient"),
                "permission": _may("oauth_core.view_oauthclient"),
            }
        )
    if not items:
        return None
    return {"title": "Credentials", "separator": True, "collapsible": True, "items": items}


def _audit_group() -> dict[str, Any] | None:
    """The trail. Collapsed, because it is read after something went wrong."""
    items = []
    if app_installed("auth_core"):
        items.append(
            {
                "title": "Sign-in events",
                "icon": "history",
                "link": _changelist("auth_core", "authevent"),
                "permission": _may("auth_core.view_authevent"),
            }
        )
    if app_installed("oauth_core"):
        items.extend(
            [
                {
                    "title": "OAuth events",
                    "icon": "fact_check",
                    "link": _changelist("oauth_core", "oauthauditevent"),
                    "permission": _may("oauth_core.view_oauthauditevent"),
                },
                {
                    "title": "Social login attempts",
                    "icon": "swap_horiz",
                    "link": _changelist("oauth_core", "socialloginattempt"),
                    "permission": _may("oauth_core.view_socialloginattempt"),
                },
            ]
        )
    if not items:
        return None
    return {"title": "Audit", "separator": True, "collapsible": True, "items": items}


def sidebar_navigation(request: HttpRequest) -> list[dict[str, Any]]:
    """Every group the installed apps can fill, in the order they are used.

    Unfold resolves this per request, which is what lets it be a function of the
    installed apps rather than a list written before they were chosen.
    """
    groups: list[dict[str, Any] | None] = [
        {
            "title": "Overview",
            "separator": False,
            "collapsible": False,
            "items": [
                {
                    "title": "Dashboard",
                    "icon": "dashboard",
                    "link": reverse_lazy("admin:index"),
                },
                {
                    "title": "API documentation",
                    "icon": "api",
                    "link": reverse_lazy("api-docs"),
                },
            ],
        },
    ]
    if apps.is_installed("apps.cms"):
        groups.append(_content_group())
    if apps.is_installed("apps.notifications"):
        groups.append(_notifications_group())
    if apps.is_installed("apps.shop"):
        groups.append(_shop_group())
    groups.extend([_people_group(), _credentials_group(), _audit_group()])
    return [group for group in groups if group is not None]


def _content_numbers() -> dict[str, Any]:
    """Counts, plus the one number an editor actually acts on: what is missing."""
    from apps.cms.models import Field, Page, Section
    from apps.cms.translations import default_language, known_languages

    languages = known_languages()
    fields = Field.objects.filter(is_active=True)
    total = fields.count()
    # A field is translated into a language when its values object has a key for
    # it, so completeness is countable without loading a single value.
    translated = {
        language: fields.filter(**{f"values__{language}__isnull": False}).count()
        for language in languages
    }
    required_missing = sum(
        1
        for field in fields.filter(required=True).only("required", "values")
        if default_language() not in field.values
    )
    return {
        # Live rather than "all": the number an editor recognises is the number
        # of pages a reader can actually reach.
        "pages": Page.objects.live().count(),
        "drafts": Page.objects.exclude(status="published").count(),
        "sections": Section.objects.filter(is_active=True).count(),
        "fields": total,
        "languages": languages,
        "coverage": [
            {
                "language": language,
                "written": written,
                "percent": round(written / total * 100) if total else 0,
            }
            for language, written in translated.items()
        ],
        "required_missing": required_missing,
        "pages_link": reverse_lazy("admin:cms_page_changelist"),
    }


def _site_events() -> dict[str, Any]:
    """The dates whose reminder window is open, soonest first.

    Its own block rather than another number on the content card, because it is
    the one thing on this page that is a list: "three things are coming up" is
    not actionable, and "the certificate expires on Tuesday" is.
    """
    from apps.cms.models import SiteEvent, due_events

    events = due_events()
    return {
        "events": [
            {
                "name": event.name,
                "kind": event.get_kind_display(),
                "date": event.next_date(),
                "days": event.days_away(),
                "overdue": event.is_overdue(),
                "url": event.url,
                "link": reverse_lazy("admin:cms_siteevent_change", args=(event.pk,)),
            }
            # Five, because a dashboard block that scrolls is a block nobody
            # reads to the bottom of, and the list itself is one click away.
            for event in events[:5]
        ],
        "total": len(events),
        "overdue": sum(1 for event in events if event.is_overdue()),
        "tracked": SiteEvent.objects.filter(is_active=True).count(),
        "link": reverse_lazy("admin:cms_siteevent_changelist"),
    }


def _shop_numbers() -> dict[str, Any]:
    """The six numbers a shopkeeper opens the admin to find out.

    Chosen the way the content block was: not "how many rows are there" but
    "what has to happen today". Money taken this week is the one number that is
    a result rather than a task, and it counts orders that were actually paid --
    a total over every row would include the baskets that were abandoned at the
    payment page and would flatter the shop every morning.
    """
    from apps.shop.models import (
        Order,
        OrderStatus,
        Product,
        ProductStatus,
        Review,
        ReviewStatus,
    )

    week_ago = timezone.now() - timedelta(days=7)
    settled = (
        OrderStatus.PAID,
        OrderStatus.PROCESSING,
        OrderStatus.SHIPPED,
        OrderStatus.COMPLETED,
    )
    paid_this_week = Order.objects.filter(status__in=settled, created_at__gte=week_ago)
    revenue = paid_this_week.aggregate(taken=Sum("total"))["taken"] or 0
    # Counted in the database rather than over `available_stock` in Python: the
    # dashboard runs on every admin page load, and a property per product is a
    # query per product.
    tracked = Product.objects.filter(status=ProductStatus.ACTIVE, track_inventory=True)
    on_hand = Case(
        When(
            has_variants=True,
            then=Sum("variants__stock", filter=Q(variants__is_active=True)),
        ),
        default=F("stock"),
    )
    counted = tracked.annotate(on_hand=on_hand)
    return {
        "orders_week": paid_this_week.count(),
        "revenue_week": revenue,
        "awaiting_payment": Order.objects.filter(status=OrderStatus.PENDING).count(),
        "to_send": Order.objects.filter(
            status__in=(OrderStatus.PAID, OrderStatus.PROCESSING)
        ).count(),
        "out_of_stock": counted.filter(
            Q(on_hand__lte=0) | Q(on_hand__isnull=True), allow_backorder=False
        ).count(),
        "low_stock": counted.filter(on_hand__gt=0, on_hand__lte=F("low_stock_threshold")).count(),
        "reviews_waiting": Review.objects.filter(status=ReviewStatus.PENDING).count(),
        "orders_link": reverse_lazy("admin:shop_order_changelist"),
        "products_link": reverse_lazy("admin:shop_product_changelist"),
        "reviews_link": reverse_lazy("admin:shop_review_changelist"),
    }


def _account_numbers() -> dict[str, Any]:
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    week_ago = timezone.now() - timedelta(days=7)
    return {
        "total": user_model.objects.count(),
        "active": user_model.objects.filter(is_active=True).count(),
        "staff": user_model.objects.filter(is_staff=True).count(),
        "recent": user_model.objects.filter(date_joined__gte=week_ago).count(),
        "link": reverse_lazy("admin:accounts_user_changelist"),
    }


def _sign_in_numbers() -> dict[str, Any] | None:
    if not app_installed("auth_core"):
        return None
    from infrastructure.auth.core.models import AuthEvent

    week_ago = timezone.now() - timedelta(days=7)
    recent = AuthEvent.objects.filter(created_at__gte=week_ago)
    totals = recent.aggregate(
        succeeded=Count("id", filter=Q(event_type="login_succeeded")),
        failed=Count("id", filter=Q(event_type="login_failed")),
    )
    by_method = (
        recent.filter(event_type="login_succeeded")
        .values("method")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    attempts = (totals["succeeded"] or 0) + (totals["failed"] or 0)
    return {
        "succeeded": totals["succeeded"] or 0,
        "failed": totals["failed"] or 0,
        "success_rate": round((totals["succeeded"] or 0) / attempts * 100) if attempts else 0,
        "by_method": [
            {"method": row["method"] or "unknown", "count": row["count"]} for row in by_method[:5]
        ],
        "link": reverse_lazy("admin:auth_core_authevent_changelist"),
    }


def dashboard(request: HttpRequest, context: dict[str, Any]) -> dict[str, Any]:
    """Fill the front page with the numbers this deployment actually has.

    Every block is optional and every block is permission-checked, so the page
    an editor opens is about content and the page an administrator opens is
    about the whole system -- and neither is shown a card they cannot follow.
    """
    user = request.user
    content = None
    if apps.is_installed("apps.cms") and (
        user.is_superuser or user.has_perm("cms.view_page") or user.has_perm("cms.change_field")
    ):
        content = _content_numbers()
    site_events = None
    if apps.is_installed("apps.cms") and (user.is_superuser or user.has_perm("cms.view_siteevent")):
        site_events = _site_events()
    shop = None
    if apps.is_installed("apps.shop") and (
        user.is_superuser or user.has_perm("shop.view_order") or user.has_perm("shop.view_product")
    ):
        shop = _shop_numbers()
    accounts = None
    if user.is_superuser or user.has_perm("accounts.view_user"):
        accounts = _account_numbers()
    sign_ins = None
    if user.is_superuser or user.has_perm("auth_core.view_authevent"):
        sign_ins = _sign_in_numbers()

    context.update(
        {
            "content_numbers": content,
            "site_events": site_events,
            "shop_numbers": shop,
            "account_numbers": accounts,
            "sign_in_numbers": sign_ins,
        }
    )
    return context
