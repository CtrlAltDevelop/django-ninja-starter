"""Drafts, schedules and the links that show them anyway."""

from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from apps.cms.models import Page, PageStatus
from apps.cms.preview import make_token

pytestmark = pytest.mark.django_db

PAGES = "/api/v1/cms/pages"


def data(response: Any) -> Any:
    assert response.status_code == 200, response.content
    return response.json()["data"]


class TestState:
    def test_a_new_page_is_a_draft(self, db: None) -> None:
        """The safe default: nothing is published by writing it."""
        page = Page.objects.create(name="New", slug="new")

        assert page.status == PageStatus.DRAFT
        assert page.is_live is False

    def test_publishing_without_a_date_is_immediate(self, draft: Page) -> None:
        draft.publish()
        draft.save()

        assert draft.is_live is True
        assert draft.is_scheduled is False

    def test_a_future_date_is_published_and_not_yet_live(self, draft: Page) -> None:
        draft.publish(at=timezone.now() + timezone.timedelta(hours=1))
        draft.save()

        assert draft.status == PageStatus.PUBLISHED
        assert draft.is_live is False
        assert draft.is_scheduled is True

    def test_the_queryset_agrees_with_the_property(self, home: Page, scheduled: Page) -> None:
        """Two implementations of "live" would drift; this is the test that they do not."""
        live = set(Page.objects.live().values_list("slug", flat=True))

        assert live == {page.slug for page in Page.objects.all() if page.is_live}
        assert live == {"home"}

    def test_a_date_that_has_passed_is_live(self, draft: Page) -> None:
        draft.publish(at=timezone.now() - timezone.timedelta(minutes=1))
        draft.save()

        assert draft.is_live is True


class TestPreviewLinks:
    def test_a_token_opens_the_draft_it_names(self, client: Client, draft: Page) -> None:
        response = client.get(f"{PAGES}/secret?preview={make_token('secret')}")

        assert data(response)["id"] == "secret"
        assert data(response)["status"] == "draft"

    def test_a_token_for_another_page_does_not(self, client: Client, draft: Page) -> None:
        """One preview link must not be a key to every unpublished page."""
        response = client.get(f"{PAGES}/secret?preview={make_token('some-other-page')}")

        assert response.status_code == 404

    def test_a_forged_token_does_not(self, client: Client, draft: Page) -> None:
        assert client.get(f"{PAGES}/secret?preview=not-a-token").status_code == 404

    def test_an_expired_token_does_not(self, client: Client, draft: Page, settings: Any) -> None:
        settings.CMS_PREVIEW_TTL_SECONDS = -1

        assert client.get(f"{PAGES}/secret?preview={make_token('secret')}").status_code == 404

    def test_a_scheduled_page_can_be_previewed_before_its_date(
        self, client: Client, scheduled: Page
    ) -> None:
        response = client.get(f"{PAGES}/launch?preview={make_token('launch')}")

        assert data(response)["id"] == "launch"
