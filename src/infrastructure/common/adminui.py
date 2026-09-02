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
from django.db.models import Count, Q
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
    accounts = None
    if user.is_superuser or user.has_perm("accounts.view_user"):
        accounts = _account_numbers()
    sign_ins = None
    if user.is_superuser or user.has_perm("auth_core.view_authevent"):
        sign_ins = _sign_in_numbers()

    context.update(
        {
            "content_numbers": content,
            "account_numbers": accounts,
            "sign_in_numbers": sign_ins,
        }
    )
    return context
