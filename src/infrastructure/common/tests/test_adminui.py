"""The admin's chrome: that it is themed, assembled from the apps, and filtered.

The sidebar and the front page are both built by walking the installed apps for
an ``adminui`` module -- see :mod:`infrastructure.common.adminui`. So the tests
here are about the *assembly*: that an app which is installed contributes, that
one which is not leaves nothing behind, that same-titled groups merge rather than
appearing twice, and that a reader is never offered a card or a link they cannot
follow.
"""

from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse

from infrastructure.common.adminui import (
    contributions,
    dashboard,
    environment_badge,
    sidebar_navigation,
)

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


def _request(user: Any) -> Any:
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


def _navigation(user: Any) -> list[Any]:
    return sidebar_navigation(_request(user))


def _sections(user: Any) -> dict[str, Any]:
    """The front page's sections, by title."""
    context = dashboard(_request(user), {})
    return {section["title"]: section for section in context["admin_sections"]}


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


class TestAssembly:
    def test_each_installed_app_may_contribute_its_own_navigation(self) -> None:
        """The point of the protocol: an app carries its own admin, or none."""
        labels = {label for _, label, _ in contributions("navigation")}

        assert "accounts" in labels
        if apps.is_installed("apps.cms"):
            assert "cms" in labels
        if apps.is_installed("apps.support"):
            assert "support" in labels

    def test_an_app_that_is_not_installed_contributes_nothing(self) -> None:
        labels = {label for _, label, _ in contributions("dashboard")}

        for app, label in (("apps.cms", "cms"), ("apps.shop", "shop")):
            if not apps.is_installed(app):
                assert label not in labels

    def test_groups_with_one_title_are_merged_rather_than_repeated(self, superuser: Any) -> None:
        """Several apps fill People and Audit; two headings would read as a bug."""
        titles = [group["title"] for group in _navigation(superuser)]

        assert len(titles) == len(set(titles))

    def test_a_group_nobody_may_follow_is_not_shown_at_all(self, editor: Any) -> None:
        """An empty heading reads as something broken, not as something absent."""
        for group in _navigation(editor):
            assert group["items"]


class TestDashboard:
    def test_it_counts_the_content_a_superuser_may_see(self, superuser: Any, home: Any) -> None:
        sections = _sections(superuser)

        content = {card["label"]: card["value"] for card in sections["Content"]["cards"]}
        people = {card["label"]: card["value"] for card in sections["People"]["cards"]}

        assert content["Published pages"] == 1
        assert content["Content fields"] == 3
        assert people["Accounts"] == 1

    def test_an_editor_gets_content_and_not_accounts(self, editor: Any, home: Any) -> None:
        """Half a dashboard beats a row of cards that answer 403."""
        sections = _sections(editor)

        assert "Content" in sections
        assert "People" not in sections

    def test_a_section_with_nothing_to_say_is_left_out(self, editor: Any, home: Any) -> None:
        """An app contributes cards or nothing; an empty heading is neither."""
        for section in _sections(editor).values():
            assert section.get("cards") or section.get("panels")

    def test_the_front_page_renders_those_numbers(
        self, client: Client, superuser: Any, home: Any
    ) -> None:
        client.force_login(superuser)

        page = client.get(reverse("admin:index")).content.decode()

        assert "Translation coverage" in page
        assert "Required, still empty" in page
        # The section heading the template draws from the contribution itself.
        assert "Content" in page
