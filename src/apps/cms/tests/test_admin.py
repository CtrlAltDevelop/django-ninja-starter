"""The content screen: who may open it, what it shows, and what saving does."""

from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse

from apps.cms import theme
from apps.cms.fields import FieldType
from apps.cms.forms import field_key
from apps.cms.models import Field, Page, Section

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def superuser() -> Any:
    return User.objects.create_superuser(username="root", email="root@example.com", password="x")


@pytest.fixture
def editor() -> Any:
    """Staff with exactly one permission: change content."""
    from django.contrib.auth.models import Permission

    user = User.objects.create_user(username="edie", password="x", is_staff=True)
    user.user_permissions.add(
        Permission.objects.get(codename="change_field", content_type__app_label="cms"),
        Permission.objects.get(codename="view_page", content_type__app_label="cms"),
    )
    return user


def content_url(page: Page) -> str:
    return reverse("admin:cms_page_content", args=(page.pk,))


def _post(page: Page, **overrides: Any) -> dict[str, Any]:
    """Every input the form renders, so a save is not a partial submission."""
    fields = {field.slug: field for field in Field.objects.filter(section__page=page)}
    payload = {
        field_key(fields["headline"]): "Welcome",
        field_key(fields["background"]): "https://cdn.example.com/hero.jpg",
        f"{field_key(fields['background'])}__title": "",
        f"{field_key(fields['background'])}__alt": "",
        field_key(fields["price"]): "9",
    }
    return {**payload, **overrides}


class TestPermissions:
    def test_an_anonymous_visitor_is_sent_to_the_login_page(
        self, client: Client, home: Page
    ) -> None:
        response = client.get(content_url(home))

        assert response.status_code == 302
        assert "/admin/login/" in response["Location"]

    def test_an_editor_may_open_it(self, client: Client, home: Page, editor: Any) -> None:
        client.force_login(editor)

        assert client.get(content_url(home)).status_code == 200

    def test_staff_without_the_permission_may_not(
        self, client: Client, home: Page, superuser: Any
    ) -> None:
        plain = User.objects.create_user(username="nobody", password="x", is_staff=True)
        client.force_login(plain)

        assert client.get(content_url(home)).status_code == 403

    def test_an_editor_sees_the_structure_and_cannot_save_it(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        """Read-only rather than hidden: an editor has to be able to see what exists."""
        client.force_login(editor)
        response = client.get(reverse("admin:cms_page_change", args=(home.pk,)))

        assert response.status_code == 200
        assert 'name="_save"' not in response.content.decode()


class TestContentScreen:
    def test_it_shows_the_sections_in_page_order(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        client.force_login(editor)
        body = client.get(content_url(home)).content.decode()

        assert body.index("Hero") < body.index("Plans") < body.index("Basic")

    def test_it_offers_every_configured_language(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        client.force_login(editor)
        body = client.get(content_url(home)).content.decode()

        assert f"{content_url(home)}?language=fa" in body

    def test_a_second_language_starts_empty_rather_than_prefilled(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        """Prefilling would let a save declare the English copy to be Persian."""
        client.force_login(editor)
        background = Field.objects.get(slug="background")

        form = client.get(f"{content_url(home)}?language=fa").context["form"]

        assert form.initial[field_key(background)] == ""

    def test_saving_writes_only_the_language_being_edited(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        client.force_login(editor)
        headline = Field.objects.get(slug="headline")

        response = client.post(
            f"{content_url(home)}?language=fa",
            _post(home, **{field_key(headline): "سلام"}),
        )

        headline.refresh_from_db()
        assert response.status_code == 302
        assert headline.values == {"en-us": "Welcome", "fa": "سلام"}

    def test_an_emptied_box_removes_that_translation(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        client.force_login(editor)
        headline = Field.objects.get(slug="headline")

        client.post(f"{content_url(home)}?language=fa", _post(home, **{field_key(headline): ""}))

        headline.refresh_from_db()
        assert headline.values == {"en-us": "Welcome"}

    def test_a_url_and_its_alt_text_are_stored_as_one_value(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        client.force_login(editor)
        background = Field.objects.get(slug="background")

        client.post(
            content_url(home),
            _post(home, **{f"{field_key(background)}__alt": "A mountain"}),
        )

        background.refresh_from_db()
        assert background.values["en-us"]["alt"] == "A mountain"

    def test_a_bad_value_is_reported_against_its_own_box(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        client.force_login(editor)
        background = Field.objects.get(slug="background")

        response = client.post(content_url(home), _post(home, **{field_key(background): "nope"}))

        assert response.status_code == 200
        assert field_key(background) in response.context["form"].errors
        background.refresh_from_db()
        assert background.values["en-us"]["url"] == "https://cdn.example.com/hero.jpg"

    def test_the_default_language_insists_on_required_content(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        client.force_login(editor)
        headline = Field.objects.get(slug="headline")

        response = client.post(content_url(home), _post(home, **{field_key(headline): ""}))

        assert field_key(headline) in response.context["form"].errors

    def test_another_language_does_not(self, client: Client, home: Page, editor: Any) -> None:
        client.force_login(editor)
        headline = Field.objects.get(slug="headline")

        response = client.post(
            f"{content_url(home)}?language=fa", _post(home, **{field_key(headline): ""})
        )

        assert response.status_code == 302

    def test_an_inactive_section_is_still_editable(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        """Turning a section off must not lock the copy inside it."""
        client.force_login(editor)
        Section.objects.filter(slug="hero").update(is_active=False)

        body = client.get(content_url(home)).content.decode()

        assert "Hero" in body


class TestUploadingOnTheContentScreen:
    """The end an editor sees: pick a file, save, and the picture is on the page."""

    @pytest.fixture(autouse=True)
    def media(self, tmp_path: Path) -> Any:
        with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
            yield tmp_path

    def test_the_form_is_multipart_when_a_field_can_carry_a_file(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        """Without the enctype the page saves, says so, and drops every file."""
        client.force_login(editor)

        body = client.get(content_url(home)).content.decode()

        assert 'enctype="multipart/form-data"' in body

    def test_an_image_field_offers_both_ways_of_answering(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        client.force_login(editor)
        background = Field.objects.get(slug="background")

        body = client.get(content_url(home)).content.decode()

        assert f'name="{field_key(background)}__upload"' in body
        assert f'name="{field_key(background)}"' in body

    def test_the_current_picture_is_shown_back(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        """An address in a box is not a picture, and this screen replaces pictures."""
        client.force_login(editor)

        body = client.get(content_url(home)).content.decode()

        assert "https://cdn.example.com/hero.jpg" in body

    def test_a_picked_file_is_stored_and_becomes_the_value(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        client.force_login(editor)
        background = Field.objects.get(slug="background")
        headline = Field.objects.get(slug="headline")

        response = client.post(
            content_url(home),
            {
                field_key(headline): "Welcome",
                field_key(background): "",
                f"{field_key(background)}__upload": SimpleUploadedFile("hero.png", b"x"),
                field_key(Field.objects.get(slug="price")): "9",
            },
        )

        assert response.status_code == 302, response.content
        background.refresh_from_db()
        assert background.values["en-us"]["url"].startswith("/media/cms/uploads/image/hero-")


class TestEveryTypeRenders:
    """A type nobody put on a page is a type nobody found out was broken."""

    def test_the_content_screen_renders_one_field_of_every_type(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        client.force_login(editor)
        section = Section.objects.create(page=home, name="All", slug="all", order=99)
        for index, field_type in enumerate(FieldType.values):
            Field.objects.create(
                section=section,
                name=field_type,
                slug=f"f-{field_type}",
                field_type=field_type,
                order=index,
                options=["small", "large"] if field_type == FieldType.SELECT else [],
            )

        response = client.get(content_url(home))

        assert response.status_code == 200, response.content

    def test_and_renders_a_list_of_every_type_too(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        client.force_login(editor)
        section = Section.objects.create(page=home, name="Lists", slug="lists", order=98)
        for index, field_type in enumerate(FieldType.values):
            Field.objects.create(
                section=section,
                name=field_type,
                slug=f"l-{field_type}",
                field_type=field_type,
                multiple=True,
                order=index,
                options=["small", "large"] if field_type == FieldType.SELECT else [],
            )

        response = client.get(content_url(home))

        assert response.status_code == 200, response.content


class TestStructureAdmin:
    def test_the_page_list_links_to_the_content_screen(
        self, client: Client, home: Page, superuser: Any
    ) -> None:
        client.force_login(superuser)
        body = client.get(reverse("admin:cms_page_changelist")).content.decode()

        assert content_url(home) in body

    def test_a_superuser_can_add_a_field_to_a_section(
        self, client: Client, home: Page, superuser: Any
    ) -> None:
        client.force_login(superuser)
        hero = Section.objects.get(slug="hero")

        assert client.get(reverse("admin:cms_section_change", args=(hero.pk,))).status_code == 200
        assert Field.objects.create(
            section=hero, name="Subhead", slug="subhead", field_type=FieldType.TEXTAREA
        ).pk


class TestSiteSettingsForm:
    """The site's copy, one input per language rather than one JSON box."""

    def test_it_offers_an_input_per_configured_language(
        self, client: Client, superuser: Any, site: Any
    ) -> None:
        client.force_login(superuser)

        page = client.get(
            reverse("admin:cms_sitesettings_change", args=(site.pk,))
        ).content.decode()

        assert 'name="name__en-us"' in page
        assert 'name="name__fa"' in page
        assert "Example Co" not in page  # the fixture's name is Acme

    def test_saving_writes_each_language_into_the_json_column(
        self, client: Client, superuser: Any, site: Any
    ) -> None:
        client.force_login(superuser)

        response = client.post(
            reverse("admin:cms_sitesettings_change", args=(site.pk,)),
            {
                "name__en-us": "Acme",
                "name__fa": "آکمی",
                "tagline__en-us": "We make things",
                "tagline__fa": "",
                "description__en-us": "The Acme site",
                "description__fa": "",
                "og_title__en-us": "Acme — we make things",
                "og_title__fa": "",
                "og_description__en-us": "Everything Acme makes.",
                "og_description__fa": "",
                "logo": "https://cdn.example.com/logo.svg",
                "favicon": "",
                "og_image": "https://cdn.example.com/card.png",
                "og_url": "",
                "contact": '{"email": "hello@example.com"}',
                "social_links": "[]",
                "extra": "{}",
                "_save": "Save",
            },
        )

        site.refresh_from_db()
        assert response.status_code == 302, response.content
        assert site.name == {"en-us": "Acme", "fa": "آکمی"}
        assert site.og_title == {"en-us": "Acme — we make things"}
        assert site.og_image == "https://cdn.example.com/card.png"

    def test_a_language_left_empty_is_removed_rather_than_stored_blank(
        self, client: Client, superuser: Any, site: Any
    ) -> None:
        client.force_login(superuser)

        client.post(
            reverse("admin:cms_sitesettings_change", args=(site.pk,)),
            {
                "name__en-us": "Acme",
                "name__fa": "",
                "tagline__en-us": "",
                "tagline__fa": "",
                "description__en-us": "",
                "description__fa": "",
                "og_title__en-us": "",
                "og_title__fa": "",
                "og_description__en-us": "",
                "og_description__fa": "",
                "logo": "",
                "favicon": "",
                "og_image": "",
                "og_url": "",
                "contact": "{}",
                "social_links": "[]",
                "extra": "{}",
                "_save": "Save",
            },
        )

        site.refresh_from_db()
        assert site.name == {"en-us": "Acme"}


class TestTheme:
    def test_the_content_screen_is_part_of_the_themed_admin(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        """Not a page that happens to live at an admin URL: the same application."""
        client.force_login(editor)

        page = client.get(content_url(home)).content.decode()

        # The admin's own date JS needs both of these, and a custom page is
        # where they are easiest to forget.
        assert "/admin/jsi18n/" in page
        assert "admin/js/core.js" in page
        if theme.UNFOLD_INSTALLED:
            assert "unfold/css/styles.css" in page

    def test_it_renders_whatever_theme_the_project_has(
        self, client: Client, home: Page, editor: Any
    ) -> None:
        """The one line that differs between a themed project and a plain one.

        Asserted rather than assumed, because this app is meant to be copied
        into projects that have never heard of Unfold, and the failure mode
        there is a template that does not exist.
        """
        client.force_login(editor)

        response = client.get(content_url(home))

        assert response.status_code == 200
        assert response.context["base_template"] == theme.ADMIN_BASE_TEMPLATE
        assert (
            "unfold/layouts/base_simple.html" if theme.UNFOLD_INSTALLED else "admin/base_site.html"
        ) == theme.ADMIN_BASE_TEMPLATE

    def test_a_library_section_has_a_content_screen_of_its_own(
        self, client: Client, home_with_footer: Page, footer: Any, editor: Any
    ) -> None:
        client.force_login(editor)

        response = client.get(reverse("admin:cms_section_content", args=(footer.pk,)))

        assert response.status_code == 200
        assert "Shared section, on 1 page(s)" in response.content.decode()

    def test_the_page_screen_badges_a_shared_section(
        self, client: Client, home_with_footer: Page, editor: Any
    ) -> None:
        """Editing this changes every page it is placed on; nothing else says so."""
        client.force_login(editor)

        page = client.get(content_url(home_with_footer)).content.decode()

        assert "shared" in page
        assert "edit on its own" in page


class TestPublishingFromTheAdmin:
    def test_the_action_publishes_the_selected_pages(
        self, client: Client, draft: Page, superuser: Any
    ) -> None:
        client.force_login(superuser)

        client.post(
            reverse("admin:cms_page_changelist"),
            {"action": "publish_now", "_selected_action": [str(draft.pk)], "index": 0},
        )

        draft.refresh_from_db()
        assert draft.is_live is True

    def test_and_moves_them_back(self, client: Client, home: Page, superuser: Any) -> None:
        client.force_login(superuser)

        client.post(
            reverse("admin:cms_page_changelist"),
            {"action": "unpublish", "_selected_action": [str(home.pk)], "index": 0},
        )

        home.refresh_from_db()
        assert home.is_live is False

    def test_the_preview_link_is_on_the_page_it_belongs_to(
        self, client: Client, draft: Page, superuser: Any
    ) -> None:
        """And it opens the draft, which is the only reason it exists."""
        client.force_login(superuser)

        page = client.get(reverse("admin:cms_page_change", args=(draft.pk,))).content.decode()
        [link] = [part for part in page.split('"') if part.startswith("/api/v1/cms/pages/")]

        assert client.get(link).status_code == 200

    def test_the_content_screen_says_whether_anybody_is_reading(
        self, client: Client, draft: Page, editor: Any
    ) -> None:
        client.force_login(editor)

        page = client.get(content_url(draft)).content.decode()

        assert "draft" in page
        assert "?preview=" in page


class TestDuplication:
    def test_it_copies_structure_and_content_as_a_draft(
        self, client: Client, home_with_footer: Page, superuser: Any
    ) -> None:
        client.force_login(superuser)

        client.post(
            reverse("admin:cms_page_changelist"),
            {"action": "duplicate", "_selected_action": [str(home_with_footer.pk)], "index": 0},
        )

        copy = Page.objects.get(slug="home-copy")
        assert copy.status == "draft"
        assert [section.slug for section in copy.sections.filter(parent__isnull=True)] == [
            "hero",
            "plans",
        ]
        assert copy.sections.get(slug="hero").fields.get(slug="headline").values == {
            "en-us": "Welcome",
            "fa": "خوش آمدید",
        }

    def test_the_nested_section_comes_too(self, client: Client, home: Page, superuser: Any) -> None:
        client.force_login(superuser)

        client.post(
            reverse("admin:cms_page_changelist"),
            {"action": "duplicate", "_selected_action": [str(home.pk)], "index": 0},
        )

        copy = Page.objects.get(slug="home-copy")
        plans = copy.sections.get(slug="plans")
        assert [child.slug for child in plans.children.all()] == ["basic"]
        assert plans.children.get(slug="basic").fields.get(slug="price").values == {"en-us": 9}

    def test_a_shared_section_is_placed_again_rather_than_copied(
        self, client: Client, home_with_footer: Page, footer: Any, superuser: Any
    ) -> None:
        """Copying it would be the thing the library exists to prevent."""
        client.force_login(superuser)

        client.post(
            reverse("admin:cms_page_changelist"),
            {"action": "duplicate", "_selected_action": [str(home_with_footer.pk)], "index": 0},
        )

        copy = Page.objects.get(slug="home-copy")
        assert [p.section_id for p in copy.placements.all()] == [footer.pk]
        assert Section.objects.filter(slug="footer").count() == 1

    def test_copying_twice_does_not_collide(
        self, client: Client, home: Page, superuser: Any
    ) -> None:
        client.force_login(superuser)

        for _ in range(2):
            client.post(
                reverse("admin:cms_page_changelist"),
                {"action": "duplicate", "_selected_action": [str(home.pk)], "index": 0},
            )

        assert Page.objects.filter(slug__startswith="home-copy").count() == 2
        assert Page.objects.filter(slug="home-copy-2").exists()


class TestFieldCompleteness:
    def test_the_filter_finds_required_fields_nobody_has_written(
        self, client: Client, home: Page, superuser: Any
    ) -> None:
        client.force_login(superuser)
        Field.objects.create(
            section=Section.objects.get(slug="hero"),
            name="Subhead",
            slug="subhead",
            required=True,
        )

        page = client.get(f"{reverse('admin:cms_field_changelist')}?filled=missing")

        assert [field.slug for field in page.context["cl"].queryset] == ["subhead"]

    def test_and_the_ones_somebody_has(self, client: Client, home: Page, superuser: Any) -> None:
        client.force_login(superuser)

        page = client.get(f"{reverse('admin:cms_field_changelist')}?filled=done")

        assert [field.slug for field in page.context["cl"].queryset] == ["headline"]
