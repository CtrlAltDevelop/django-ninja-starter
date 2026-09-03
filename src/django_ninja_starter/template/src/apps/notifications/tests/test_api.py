"""The HTTP half: the history a socket never sends, and everything an account
can do to what is in it.

Every test here goes through a real bearer token from the project's own issuer,
because "the socket accepts this credential and the API does not" is precisely
the kind of drift these endpoints are worth protecting against.
"""

from typing import Any

import pytest
from django.test import Client

from apps.notifications.events import notify_user
from apps.notifications.models import Notification, unread_count
from apps.notifications.services import MAX_PAGE
from apps.notifications.tests.conftest import access_token

LIST = "/api/v1/notifications"
UNREAD = "/api/v1/notifications/unread-count"
READ_ALL = "/api/v1/notifications/read-all"
DISMISS_ALL = "/api/v1/notifications/dismiss-all"

CHANGES = ["read", "unread", "dismiss", "restore"]


def _bearer(user: Any) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token(user)}"}


@pytest.fixture
def client() -> Client:
    return Client()


def _rows(client: Client, user: Any, query: str = "") -> list[dict[str, Any]]:
    return client.get(f"{LIST}{query}", **_bearer(user)).json()["data"]["notifications"]


# -- authentication ---------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", LIST),
        ("get", UNREAD),
        ("post", READ_ALL),
        ("post", DISMISS_ALL),
        ("get", f"{LIST}/00000000-0000-0000-0000-000000000000"),
        *[("post", f"{LIST}/00000000-0000-0000-0000-000000000000/{change}") for change in CHANGES],
    ],
)
def test_every_endpoint_needs_a_credential(
    client: Client, method: str, path: str, db: None
) -> None:
    assert getattr(client, method)(path).status_code == 401


# -- listing ----------------------------------------------------------------


def test_the_list_mixes_what_is_addressed_to_everybody_with_what_is_mine(
    client: Client, announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    subjects = [row["subject"] for row in _rows(client, alice)]

    assert set(subjects) == {announcement.subject, for_alice.subject}


def test_the_list_never_shows_another_accounts_notifications(
    client: Client, for_bob: Notification, alice: Any
) -> None:
    assert _rows(client, alice) == []


def test_every_row_carries_whether_this_account_has_read_it(
    client: Client, for_alice: Notification, alice: Any
) -> None:
    assert _rows(client, alice)[0]["read"] is False

    client.post(f"{LIST}/{for_alice.pk}/read", **_bearer(alice))

    assert _rows(client, alice)[0]["read"] is True


def test_every_row_carries_whether_it_has_been_dismissed(
    client: Client, for_alice: Notification, alice: Any
) -> None:
    client.post(f"{LIST}/{for_alice.pk}/dismiss", **_bearer(alice))

    assert _rows(client, alice, "?include_dismissed=true")[0]["dismissed"] is True


def test_the_unread_filter_narrows_to_what_is_outstanding(
    client: Client, announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    client.post(f"{LIST}/{for_alice.pk}/read", **_bearer(alice))

    unread = _rows(client, alice, "?unread=true")
    read = _rows(client, alice, "?unread=false")

    assert [row["subject"] for row in unread] == [announcement.subject]
    assert [row["subject"] for row in read] == [for_alice.subject]


def test_the_level_filter_narrows_to_one_severity(client: Client, alice: Any) -> None:
    notify_user(alice, "Fine", level="info")
    notify_user(alice, "Broken", level="error")

    assert [row["subject"] for row in _rows(client, alice, "?level=error")] == ["Broken"]


def test_the_audience_filter_separates_announcements_from_mail(
    client: Client, announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    rows = _rows(client, alice, "?audience=global")

    assert [row["subject"] for row in rows] == [announcement.subject]


def test_an_unknown_filter_value_is_a_422_rather_than_an_empty_list(
    client: Client, alice: Any
) -> None:
    """The schema knows the choices, so a typo is answerable instead of silent."""
    assert client.get(f"{LIST}?level=loud", **_bearer(alice)).status_code == 422


def test_dismissed_rows_are_left_out_unless_asked_for(
    client: Client, for_alice: Notification, alice: Any
) -> None:
    client.post(f"{LIST}/{for_alice.pk}/dismiss", **_bearer(alice))

    assert _rows(client, alice) == []
    assert len(_rows(client, alice, "?include_dismissed=true")) == 1


def test_the_page_size_is_capped_however_large_a_client_asks_for(
    client: Client, alice: Any
) -> None:
    """Otherwise one request can ask the database for the whole table."""
    Notification.objects.bulk_create(
        Notification(audience="user", recipient=alice, subject=f"#{index}")
        for index in range(MAX_PAGE + 10)
    )

    assert len(_rows(client, alice, "?limit=5000")) == MAX_PAGE


def test_paging_walks_the_list_without_repeating_a_row(client: Client, alice: Any) -> None:
    Notification.objects.bulk_create(
        Notification(audience="user", recipient=alice, subject=f"#{index}") for index in range(5)
    )

    first = _rows(client, alice, "?limit=2&offset=0")
    second = _rows(client, alice, "?limit=2&offset=2")

    assert {row["id"] for row in first}.isdisjoint({row["id"] for row in second})


def test_the_page_says_how_much_there_was_to_page_through(client: Client, alice: Any) -> None:
    """So a client can render "showing 2 of 5" without fetching all five."""
    Notification.objects.bulk_create(
        Notification(audience="user", recipient=alice, subject=f"#{index}") for index in range(5)
    )

    body = client.get(f"{LIST}?limit=2", **_bearer(alice)).json()["data"]

    assert (len(body["notifications"]), body["total"], body["limit"], body["offset"]) == (
        2,
        5,
        2,
        0,
    )


def test_the_total_respects_the_filters_it_was_asked_with(
    client: Client, announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    body = client.get(f"{LIST}?audience=global", **_bearer(alice)).json()["data"]

    assert body["total"] == 1


# -- one notification -------------------------------------------------------


def test_one_notification_can_be_fetched_by_id(
    client: Client, for_alice: Notification, alice: Any
) -> None:
    body = client.get(f"{LIST}/{for_alice.pk}", **_bearer(alice)).json()["data"]

    assert body["subject"] == for_alice.subject


def test_a_dismissed_notification_is_still_fetchable_by_id(
    client: Client, for_alice: Notification, alice: Any
) -> None:
    """A link to something cleared away should open it, not read as somebody else's."""
    client.post(f"{LIST}/{for_alice.pk}/dismiss", **_bearer(alice))

    assert client.get(f"{LIST}/{for_alice.pk}", **_bearer(alice)).status_code == 200


def test_fetching_somebody_elses_notification_is_a_404(
    client: Client, for_bob: Notification, alice: Any
) -> None:
    assert client.get(f"{LIST}/{for_bob.pk}", **_bearer(alice)).status_code == 404


def test_the_list_path_is_not_shadowed_by_the_by_id_one(client: Client, alice: Any) -> None:
    """`/unread-count` and `/{id}` are both GETs under the same prefix."""
    assert client.get(UNREAD, **_bearer(alice)).status_code == 200


# -- changing state ---------------------------------------------------------


@pytest.mark.parametrize("change", CHANGES)
def test_every_change_answers_the_same_shape(
    client: Client, change: str, for_alice: Notification, alice: Any
) -> None:
    body = client.post(f"{LIST}/{for_alice.pk}/{change}", **_bearer(alice)).json()["data"]

    assert set(body) == {"id", "unread", "changed"}
    assert body["id"] == str(for_alice.pk)


@pytest.mark.parametrize("change", CHANGES)
def test_every_change_refuses_another_accounts_mail_with_a_404_not_a_403(
    client: Client, change: str, for_bob: Notification, alice: Any, bob: Any
) -> None:
    """A 403 would confirm that the notification exists, which is the leak."""
    response = client.post(f"{LIST}/{for_bob.pk}/{change}", **_bearer(alice))

    assert response.status_code == 404
    assert unread_count(bob) == 1


@pytest.mark.parametrize("change", CHANGES)
def test_every_change_on_a_notification_that_does_not_exist_is_a_404(
    client: Client, change: str, alice: Any
) -> None:
    missing = "00000000-0000-0000-0000-000000000000"

    assert client.post(f"{LIST}/{missing}/{change}", **_bearer(alice)).status_code == 404


def test_marking_one_read_answers_with_the_new_badge_number(
    client: Client, announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    body = client.post(f"{LIST}/{for_alice.pk}/read", **_bearer(alice)).json()["data"]

    assert body == {"id": str(for_alice.pk), "unread": 1, "changed": True}


def test_repeating_a_change_is_success_with_changed_false(
    client: Client, for_alice: Notification, alice: Any
) -> None:
    client.post(f"{LIST}/{for_alice.pk}/read", **_bearer(alice))
    body = client.post(f"{LIST}/{for_alice.pk}/read", **_bearer(alice)).json()["data"]

    assert body["changed"] is False


def test_marking_one_unread_puts_it_back_in_the_badge(
    client: Client, for_alice: Notification, alice: Any
) -> None:
    client.post(f"{LIST}/{for_alice.pk}/read", **_bearer(alice))
    body = client.post(f"{LIST}/{for_alice.pk}/unread", **_bearer(alice)).json()["data"]

    assert body["unread"] == 1


def test_dismissing_clears_it_from_the_tray_and_the_badge(
    client: Client, for_alice: Notification, alice: Any
) -> None:
    body = client.post(f"{LIST}/{for_alice.pk}/dismiss", **_bearer(alice)).json()["data"]

    assert body["unread"] == 0
    assert _rows(client, alice) == []


def test_dismissing_a_broadcast_leaves_it_for_everybody_else(
    client: Client, announcement: Notification, alice: Any, bob: Any
) -> None:
    """The reason dismissing is a receipt and never a delete."""
    client.post(f"{LIST}/{announcement.pk}/dismiss", **_bearer(alice))

    assert _rows(client, alice) == []
    assert len(_rows(client, bob)) == 1


def test_restoring_puts_a_dismissed_one_back(
    client: Client, for_alice: Notification, alice: Any
) -> None:
    client.post(f"{LIST}/{for_alice.pk}/dismiss", **_bearer(alice))
    client.post(f"{LIST}/{for_alice.pk}/restore", **_bearer(alice))

    assert len(_rows(client, alice)) == 1


# -- everything at once -----------------------------------------------------


def test_the_badge_counts_only_what_is_unread(
    client: Client, announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert client.get(UNREAD, **_bearer(alice)).json()["data"]["count"] == 2

    client.post(READ_ALL, **_bearer(alice))

    assert client.get(UNREAD, **_bearer(alice)).json()["data"]["count"] == 0


def test_marking_everything_read_reports_how_much_it_changed(
    client: Client, announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert client.post(READ_ALL, **_bearer(alice)).json()["data"] == {"count": 2, "unread": 0}
    assert client.post(READ_ALL, **_bearer(alice)).json()["data"] == {"count": 0, "unread": 0}


def test_emptying_the_tray_clears_the_list_and_the_badge(
    client: Client, announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert client.post(DISMISS_ALL, **_bearer(alice)).json()["data"] == {"count": 2, "unread": 0}
    assert _rows(client, alice) == []
    assert client.get(UNREAD, **_bearer(alice)).json()["data"]["count"] == 0


def test_emptying_the_tray_leaves_other_accounts_alone(
    client: Client, announcement: Notification, alice: Any, bob: Any
) -> None:
    client.post(DISMISS_ALL, **_bearer(alice))

    assert len(_rows(client, bob)) == 1


def test_emptying_an_empty_tray_reports_nothing_changed(client: Client, alice: Any) -> None:
    assert client.post(DISMISS_ALL, **_bearer(alice)).json()["data"] == {"count": 0, "unread": 0}


# -- the socket, described where a reader will find it ----------------------


def test_the_socket_is_documented_in_the_tag_swagger_renders(client: Client) -> None:
    """The one part of this app that cannot be an operation still has to be findable.

    OpenAPI has no vocabulary for a duplex connection and Swagger has no
    transport to open one, so publishing a path for the socket would render an
    operation whose "Try it out" is guaranteed to fail. The group heading these
    endpoints already sit under is the honest place for it -- which only works
    while the description actually says where the socket is and what it speaks.
    """
    from django.conf import settings

    schema = client.get("/api/v1/openapi.json").json()
    tag = next(item for item in schema["tags"] if item["name"] == "Notifications")

    assert settings.NOTIFICATIONS_WS_PATH in tag["description"]
    for command in (
        "authenticate",
        "deauthenticate",
        "whoami",
        "list",
        "get",
        "count",
        "read",
        "unread_one",
        "read_all",
        "dismiss",
        "restore",
        "dismiss_all",
        "ping",
    ):
        assert command in tag["description"], command
    for frame in ("ready", "authenticated", "notification", "state", "pong", "error"):
        assert frame in tag["description"], frame
    # No path may claim to be the socket: that is the promise this replaces.
    assert not [path for path in schema["paths"] if path.endswith(settings.NOTIFICATIONS_WS_PATH)]


def test_every_socket_command_the_documentation_names_actually_exists() -> None:
    """The tag is prose, so nothing but this stops it drifting from the dispatch table."""
    import re

    from django.conf import settings

    from apps.notifications.sockets import NotificationSocket

    documented = set(re.findall(r"`([a-z_]+)`", settings.NOTIFICATIONS_SOCKET_DOCS))
    implemented = set(NotificationSocket.commands())

    assert implemented <= documented, implemented - documented
