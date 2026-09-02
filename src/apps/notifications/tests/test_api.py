"""The HTTP half: the history a socket never sends, and marking things read.

Every test here goes through a real bearer token from the project's own issuer,
because "the socket accepts this credential and the API does not" is precisely
the kind of drift these endpoints are worth protecting against.
"""

from typing import Any

import pytest
from django.test import Client

from apps.notifications.models import Notification, unread_count
from apps.notifications.tests.conftest import access_token

LIST = "/api/v1/notifications"
UNREAD = "/api/v1/notifications/unread-count"
READ_ALL = "/api/v1/notifications/read-all"


def _bearer(user: Any) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token(user)}"}


@pytest.fixture
def client() -> Client:
    return Client()


def test_the_list_needs_a_credential(client: Client, db: None) -> None:
    assert client.get(LIST).status_code == 401


def test_the_list_mixes_what_is_addressed_to_everybody_with_what_is_mine(
    client: Client, announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    subjects = [row["subject"] for row in client.get(LIST, **_bearer(alice)).json()["data"]]

    assert set(subjects) == {announcement.subject, for_alice.subject}


def test_the_list_never_shows_another_accounts_notifications(
    client: Client, for_bob: Notification, alice: Any
) -> None:
    assert client.get(LIST, **_bearer(alice)).json()["data"] == []


def test_every_row_carries_whether_this_account_has_read_it(
    client: Client, for_alice: Notification, alice: Any
) -> None:
    assert client.get(LIST, **_bearer(alice)).json()["data"][0]["read"] is False

    client.post(f"{LIST}/{for_alice.pk}/read", **_bearer(alice))

    assert client.get(LIST, **_bearer(alice)).json()["data"][0]["read"] is True


def test_the_unread_filter_narrows_to_what_is_outstanding(
    client: Client, announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    client.post(f"{LIST}/{for_alice.pk}/read", **_bearer(alice))

    unread = client.get(f"{LIST}?unread=true", **_bearer(alice)).json()["data"]
    read = client.get(f"{LIST}?unread=false", **_bearer(alice)).json()["data"]

    assert [row["subject"] for row in unread] == [announcement.subject]
    assert [row["subject"] for row in read] == [for_alice.subject]


def test_the_page_size_is_capped_however_large_a_client_asks_for(
    client: Client, alice: Any
) -> None:
    """Otherwise one request can ask the database for the whole table."""
    Notification.objects.bulk_create(
        Notification(audience="user", recipient=alice, subject=f"#{index}") for index in range(210)
    )

    assert len(client.get(f"{LIST}?limit=5000", **_bearer(alice)).json()["data"]) == 200


def test_paging_walks_the_list_without_repeating_a_row(client: Client, alice: Any) -> None:
    Notification.objects.bulk_create(
        Notification(audience="user", recipient=alice, subject=f"#{index}") for index in range(5)
    )

    first = client.get(f"{LIST}?limit=2&offset=0", **_bearer(alice)).json()["data"]
    second = client.get(f"{LIST}?limit=2&offset=2", **_bearer(alice)).json()["data"]

    assert {row["id"] for row in first}.isdisjoint({row["id"] for row in second})


def test_the_badge_counts_only_what_is_unread(
    client: Client, announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert client.get(UNREAD, **_bearer(alice)).json()["data"]["count"] == 2

    client.post(READ_ALL, **_bearer(alice))

    assert client.get(UNREAD, **_bearer(alice)).json()["data"]["count"] == 0


def test_marking_one_read_answers_with_the_new_badge_number(
    client: Client, announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    body = client.post(f"{LIST}/{for_alice.pk}/read", **_bearer(alice)).json()["data"]

    assert body == {"id": str(for_alice.pk), "unread": 1}


def test_marking_everything_read_reports_how_much_it_changed(
    client: Client, announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert client.post(READ_ALL, **_bearer(alice)).json()["data"] == {"count": 2, "unread": 0}
    assert client.post(READ_ALL, **_bearer(alice)).json()["data"] == {"count": 0, "unread": 0}


def test_reading_somebody_elses_notification_is_a_404_not_a_403(
    client: Client, for_bob: Notification, alice: Any, bob: Any
) -> None:
    """A 403 would confirm that the notification exists, which is the leak."""
    response = client.post(f"{LIST}/{for_bob.pk}/read", **_bearer(alice))

    assert response.status_code == 404
    assert unread_count(bob) == 1


def test_reading_a_notification_that_does_not_exist_is_a_404(client: Client, alice: Any) -> None:
    missing = "00000000-0000-0000-0000-000000000000"

    assert client.post(f"{LIST}/{missing}/read", **_bearer(alice)).status_code == 404
