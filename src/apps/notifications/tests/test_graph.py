"""Reading notifications over GraphQL.

The same credential the socket and the routes accept, and the same scoping: no
field takes a user id, so no query can read another account's mail.
"""

import json
from typing import Any

import pytest
from django.test import Client

from apps.notifications.events import notify_user
from apps.notifications.models import Notification
from apps.notifications.tests.conftest import access_token

LIST = """
query($unread: Boolean, $level: String, $audience: String, $includeDismissed: Boolean! = false,
      $limit: Int! = 50) {
  notifications(unread: $unread, level: $level, audience: $audience,
                includeDismissed: $includeDismissed, limit: $limit) {
    notifications { id subject read dismissed }
    total
    limit
    offset
  }
  unreadNotificationCount
}
"""
GET_ONE = """
query($id: String!) { notification(notificationId: $id) { id subject read dismissed } }
"""
READ_ALL = "mutation { readAllNotifications { count unread } }"
DISMISS_ALL = "mutation { dismissAllNotifications { count unread } }"


def _change(name: str) -> str:
    """One mutation per verb, all with the same argument and the same reply."""
    body = "{ id unread changed }"
    return "mutation($id: String!) { " + name + "(notificationId: $id) " + body + " }"


READ_ONE = _change("readNotification")
UNREAD_ONE = _change("unreadNotification")
DISMISS_ONE = _change("dismissNotification")
RESTORE_ONE = _change("restoreNotification")

CHANGES = {
    "readNotification": READ_ONE,
    "unreadNotification": UNREAD_ONE,
    "dismissNotification": DISMISS_ONE,
    "restoreNotification": RESTORE_ONE,
}


def graphql(query: str, user: Any = None, **variables: Any) -> dict[str, Any]:
    headers = {"HTTP_AUTHORIZATION": f"Bearer {access_token(user)}"} if user else {}
    response = Client().post(
        "/graphql",
        data={"query": query, "variables": variables},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.content
    return json.loads(response.content)


def test_the_list_needs_a_credential(db: None) -> None:
    body = graphql(LIST)

    assert body["errors"][0]["extensions"]["title"] == "AUTHENTICATION_REQUIRED"


def test_the_list_mixes_what_is_addressed_to_everybody_with_what_is_mine(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    page = graphql(LIST, alice)["data"]["notifications"]

    assert {row["subject"] for row in page["notifications"]} == {
        announcement.subject,
        for_alice.subject,
    }
    assert page["total"] == 2


def test_another_account_s_mail_is_not_visible(
    for_alice: Notification, for_bob: Notification, alice: Any
) -> None:
    page = graphql(LIST, alice)["data"]["notifications"]

    assert for_bob.subject not in {row["subject"] for row in page["notifications"]}


def test_marking_one_read_reports_the_badge_number_back(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    body = graphql(READ_ONE, alice, id=str(for_alice.pk))["data"]["readNotification"]

    assert body["id"] == str(for_alice.pk)
    assert body["unread"] == 1
    assert body["changed"] is True


def test_marking_everything_read_leaves_nothing_outstanding(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    body = graphql(READ_ALL, alice)["data"]["readAllNotifications"]

    assert body["count"] == 2
    assert body["unread"] == 0


def test_a_notification_addressed_to_somebody_else_is_not_found(
    for_bob: Notification, alice: Any
) -> None:
    """A 404, not a 403: saying which would confirm another account's mail exists."""
    body = graphql(READ_ONE, alice, id=str(for_bob.pk))

    assert body["errors"][0]["extensions"]["status"] == 404


# -- the rest of the surface, matching the endpoints one for one ------------


@pytest.mark.parametrize("mutation", list(CHANGES), ids=list(CHANGES))
def test_every_change_answers_the_same_shape(
    mutation: str, for_alice: Notification, alice: Any
) -> None:
    body = graphql(CHANGES[mutation], alice, id=str(for_alice.pk))["data"][mutation]

    assert set(body) == {"id", "unread", "changed"}


@pytest.mark.parametrize("mutation", list(CHANGES), ids=list(CHANGES))
def test_every_change_needs_a_credential(mutation: str, for_alice: Notification) -> None:
    body = graphql(CHANGES[mutation], id=str(for_alice.pk))

    assert body["errors"][0]["extensions"]["title"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.parametrize("mutation", list(CHANGES), ids=list(CHANGES))
def test_every_change_refuses_another_accounts_mail(
    mutation: str, for_bob: Notification, alice: Any
) -> None:
    body = graphql(CHANGES[mutation], alice, id=str(for_bob.pk))

    assert body["errors"][0]["extensions"]["status"] == 404


@pytest.mark.parametrize("mutation", list(CHANGES), ids=list(CHANGES))
def test_every_change_refuses_an_id_that_is_not_one(mutation: str, alice: Any) -> None:
    """A malformed id and somebody else's are the same answer, for the same reason."""
    body = graphql(CHANGES[mutation], alice, id="banana")

    assert body["errors"][0]["extensions"]["status"] == 404


def test_one_notification_can_be_fetched_by_id(for_alice: Notification, alice: Any) -> None:
    body = graphql(GET_ONE, alice, id=str(for_alice.pk))["data"]["notification"]

    assert body["subject"] == for_alice.subject


def test_a_dismissed_notification_is_still_fetchable_by_id(
    for_alice: Notification, alice: Any
) -> None:
    graphql(DISMISS_ONE, alice, id=str(for_alice.pk))
    body = graphql(GET_ONE, alice, id=str(for_alice.pk))["data"]["notification"]

    assert body["dismissed"] is True


def test_fetching_somebody_elses_notification_is_not_found(
    for_bob: Notification, alice: Any
) -> None:
    body = graphql(GET_ONE, alice, id=str(for_bob.pk))

    assert body["errors"][0]["extensions"]["status"] == 404


def test_the_list_can_be_narrowed_by_audience(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    page = graphql(LIST, alice, audience="global")["data"]["notifications"]

    assert [row["subject"] for row in page["notifications"]] == [announcement.subject]
    assert page["total"] == 1


def test_the_list_can_be_narrowed_by_level(alice: Any) -> None:
    notify_user(alice, "Fine", level="info")
    notify_user(alice, "Broken", level="error")

    page = graphql(LIST, alice, level="error")["data"]["notifications"]

    assert [row["subject"] for row in page["notifications"]] == ["Broken"]


def test_dismissed_rows_are_left_out_unless_asked_for(for_alice: Notification, alice: Any) -> None:
    graphql(DISMISS_ONE, alice, id=str(for_alice.pk))

    assert graphql(LIST, alice)["data"]["notifications"]["notifications"] == []
    included = graphql(LIST, alice, includeDismissed=True)["data"]["notifications"]
    assert len(included["notifications"]) == 1


def test_the_page_reports_what_it_was_asked_for(alice: Any) -> None:
    for index in range(5):
        notify_user(alice, f"#{index}")

    page = graphql(LIST, alice, limit=2)["data"]["notifications"]

    assert (len(page["notifications"]), page["total"], page["limit"]) == (2, 5, 2)


def test_unreading_puts_it_back_in_the_badge(for_alice: Notification, alice: Any) -> None:
    graphql(READ_ONE, alice, id=str(for_alice.pk))
    body = graphql(UNREAD_ONE, alice, id=str(for_alice.pk))["data"]["unreadNotification"]

    assert body["unread"] == 1


def test_dismissing_clears_it_from_the_tray(for_alice: Notification, alice: Any) -> None:
    graphql(DISMISS_ONE, alice, id=str(for_alice.pk))

    assert graphql(LIST, alice)["data"]["notifications"]["notifications"] == []


def test_restoring_puts_it_back(for_alice: Notification, alice: Any) -> None:
    graphql(DISMISS_ONE, alice, id=str(for_alice.pk))
    graphql(RESTORE_ONE, alice, id=str(for_alice.pk))

    assert len(graphql(LIST, alice)["data"]["notifications"]["notifications"]) == 1


def test_emptying_the_tray_reports_how_much_it_cleared(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    body = graphql(DISMISS_ALL, alice)["data"]["dismissAllNotifications"]

    assert body == {"count": 2, "unread": 0}
    assert graphql(LIST, alice)["data"]["notifications"]["notifications"] == []


def test_emptying_the_tray_needs_a_credential(announcement: Notification) -> None:
    body = graphql(DISMISS_ALL)

    assert body["errors"][0]["extensions"]["title"] == "AUTHENTICATION_REQUIRED"


def test_the_badge_is_readable_on_its_own(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert graphql(LIST, alice)["data"]["unreadNotificationCount"] == 2
