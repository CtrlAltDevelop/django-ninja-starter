"""Fixtures for the CMS tests: two languages, and content worth reading.

The app is optional, so its tests are too. A project that has not named it in
``DJANGO_CMS_ENABLED`` has no CMS tables and no registered models, and importing
one raises before pytest can say anything useful -- so collection stops here
instead, and the rest of that project's suite runs as normal.
"""

from collections.abc import Iterator

import pytest
from django.apps import apps as django_apps
from django.test import override_settings
from django.utils import timezone

CMS_INSTALLED = django_apps.is_installed("apps.cms")
collect_ignore_glob = [] if CMS_INSTALLED else ["*"]

if CMS_INSTALLED:
    from apps.cms.fields import FieldType
    from apps.cms.models import (
        Field,
        Menu,
        MenuItem,
        Page,
        PageStatus,
        Section,
        SectionPlacement,
        SiteSettings,
    )

LANGUAGES = ["en-us", "fa"]


@pytest.fixture(autouse=True)
def two_languages() -> Iterator[None]:
    """Most of what this app does only shows up with a second language."""
    with override_settings(CMS_LANGUAGES=LANGUAGES):
        yield


@pytest.fixture
def site(db: None) -> SiteSettings:
    return SiteSettings.objects.create(
        name={"en-us": "Acme", "fa": "آکمی"},
        tagline={"en-us": "We make things"},
        description={"en-us": "The Acme site"},
        og_title={"en-us": "Acme — we make things"},
        og_image="https://cdn.example.com/card.png",
        logo="https://cdn.example.com/logo.svg",
        contact={"email": "hello@example.com"},
        social_links=[{"label": "X", "url": "https://x.example.com/acme"}],
    )


@pytest.fixture
def home(db: None) -> Page:
    """A published page with a section, a repeated child, and mixed field types."""
    page = Page.objects.create(
        name="Home",
        slug="home",
        title={"en-us": "Acme - Home"},
        status=PageStatus.PUBLISHED,
    )
    hero = Section.objects.create(page=page, name="Hero", slug="hero", order=0)
    Field.objects.create(
        section=hero,
        name="Headline",
        slug="headline",
        field_type=FieldType.TEXT,
        required=True,
        values={"en-us": "Welcome", "fa": "خوش آمدید"},
    )
    Field.objects.create(
        section=hero,
        name="Background",
        slug="background",
        field_type=FieldType.IMAGE,
        order=1,
        values={"en-us": "https://cdn.example.com/hero.jpg"},
    )
    plans = Section.objects.create(page=page, name="Plans", slug="plans", order=1)
    basic = Section.objects.create(page=page, parent=plans, name="Basic", slug="basic")
    Field.objects.create(
        section=basic,
        name="Price",
        slug="price",
        field_type=FieldType.NUMBER,
        values={"en-us": 9},
    )
    return page


@pytest.fixture
def footer(db: None) -> Section:
    """A library section: written once, placed on whichever pages want it."""
    section = Section.objects.create(page=None, name="Footer", slug="footer", order=99)
    Field.objects.create(
        section=section,
        name="Small print",
        slug="small-print",
        field_type=FieldType.TEXT,
        values={"en-us": "© Acme", "fa": "© آکمی"},
    )
    return section


@pytest.fixture
def home_with_footer(home: Page, footer: Section) -> Page:
    SectionPlacement.objects.create(page=home, section=footer, order=99)
    return home


@pytest.fixture
def draft(db: None) -> Page:
    """A page nobody has published, and one dated for later."""
    page = Page.objects.create(name="Secret", slug="secret")
    hero = Section.objects.create(page=page, name="Hero", slug="hero")
    Field.objects.create(
        section=hero, name="Headline", slug="headline", values={"en-us": "Not yet"}
    )
    return page


@pytest.fixture
def scheduled(db: None) -> Page:
    return Page.objects.create(
        name="Launch",
        slug="launch",
        status=PageStatus.PUBLISHED,
        published_at=timezone.now() + timezone.timedelta(days=7),
    )


@pytest.fixture
def main_menu(home: Page) -> Menu:
    menu = Menu.objects.create(name="Main", slug="main")
    MenuItem.objects.create(menu=menu, label={"en-us": "Home", "fa": "خانه"}, page=home, order=0)
    about = MenuItem.objects.create(
        menu=menu, label={"en-us": "About"}, url="https://example.com/about", order=1
    )
    MenuItem.objects.create(
        menu=menu, parent=about, label={"en-us": "Team"}, url="/about/team", order=0
    )
    return menu
