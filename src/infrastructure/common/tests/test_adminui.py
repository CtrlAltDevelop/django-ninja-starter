"""The admin's chrome: that it is themed, and that it shows what it may show."""

from typing import Any

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from infrastructure.common.adminui import dashboard, environment_badge, sidebar_navigation

User = get_user_model()


@pytest.fixture
def home(db: None) -> Any:
    """One page with three fields, so the dashboard has something to count."""
    from apps.cms.fields import FieldType
    from apps.cms.models import Field, Page, Section

    page = Page.objects.create(name="Home", slug="home", status="published")
    hero = Section.objects.create(page=page, name="Hero", slug="hero")
    for order, (slug, kind, value) in enumerate(
        [
            ("headline", FieldType.TEXT, "Welcome"),
            ("subhead", FieldType.TEXT, "Hello"),
            ("blurb", FieldType.TEXTAREA, "Longer text"),
        ]
    ):
        Field.objects.create(
            section=hero,
            name=slug.title(),
            slug=slug,
            field_type=kind,
            order=order,
            values={"en-us": value},
        )
    return page


def test_unfold_is_found_before_the_admin_it_replaces() -> None:
    """Ordering is the whole installation. Behind the admin it themes nothing."""
    installed = list(settings.INSTALLED_APPS)

    assert installed.index("unfold") < installed.index("django.contrib.admin")


@override_settings(DEBUG=True)
def test_a_development_deployment_says_so() -> None:
    assert environment_badge(RequestFactory().get("/admin/")) == ["Development", "warning"]


@override_settings(DEBUG=False)
def test_production_wears_no_badge() -> None:
    assert environment_badge(RequestFactory().get("/admin/")) is None


@pytest.fixture
def superuser(db: None) -> Any:
    return User.objects.create_superuser(username="root", email="root@example.com", password="x")


@pytest.fixture
def editor(db: None) -> Any:
    """Staff who may write content and nothing else."""
    user = User.objects.create_user(username="edie", password="x", is_staff=True)
    user.user_permissions.add(
        Permission.objects.get(codename="change_field", content_type__app_label="cms"),
        Permission.objects.get(codename="view_page", content_type__app_label="cms"),
    )
    return user


def _navigation(user: Any) -> list[dict[str, Any]]:
    request = RequestFactory().get("/admin/")
    request.user = user
    return sidebar_navigation(request)


def _titles(groups: list[dict[str, Any]]) -> set[str]:
    return {item["title"] for group in groups for item in group["items"]}


class TestSidebar:
    def test_it_only_offers_apps_that_are_installed(self, superuser: Any) -> None:
        """Every optional app absent means every link to it absent, not a 500."""
        groups = {group["title"] for group in _navigation(superuser)}

        assert "Overview" in groups
        assert "Content" in groups
        if settings.AUTH_INSTALLED_APPS:
            assert "Audit" in groups

    def test_an_editor_is_not_shown_pages_they_cannot_open(
        self, client: Client, editor: Any
    ) -> None:
        """Each item carries a permission check, and Unfold applies it per request.

        Asserted through the rendered page rather than the list, because the
        list is only half the answer -- the filtering is the other half.
        """
        client.force_login(editor)

        page = client.get(reverse("admin:index")).content.decode()

        assert "/admin/cms/page/" in page
        assert "/admin/accounts/user/" not in page
        assert "/admin/auth/group/" not in page

    def test_a_superuser_is_shown_everything(self, client: Client, superuser: Any) -> None:
        client.force_login(superuser)

        page = client.get(reverse("admin:index")).content.decode()

        for link in ("/admin/cms/page/", "/admin/cms/field/", "/admin/accounts/user/"):
            assert link in page

    def test_every_item_names_a_url_that_exists(self, superuser: Any) -> None:
        """A lazy reverse only fails when it is rendered, which is too late."""
        for group in _navigation(superuser):
            for item in group["items"]:
                assert str(item["link"]).startswith("/")


class TestDashboard:
    def test_it_counts_the_content_a_superuser_may_see(self, superuser: Any, home: Any) -> None:
        request = RequestFactory().get("/admin/")
        request.user = superuser

        context = dashboard(request, {})

        assert context["content_numbers"]["pages"] == 1
        assert context["content_numbers"]["drafts"] == 0
        assert context["content_numbers"]["fields"] == 3
        assert context["account_numbers"]["total"] == 1

    def test_an_editor_gets_content_and_not_accounts(self, editor: Any, home: Any) -> None:
        """Half a dashboard beats a row of cards that answer 403."""
        request = RequestFactory().get("/admin/")
        request.user = editor

        context = dashboard(request, {})

        assert context["content_numbers"] is not None
        assert context["account_numbers"] is None

    def test_the_front_page_renders_those_numbers(
        self, client: Client, superuser: Any, home: Any
    ) -> None:
        client.force_login(superuser)

        page = client.get(reverse("admin:index")).content.decode()

        assert "Translation coverage" in page
        assert "Required, still empty" in page
