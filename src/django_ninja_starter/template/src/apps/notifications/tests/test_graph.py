"""Reading notifications over GraphQL.

The same credential the socket and the routes accept, and the same scoping: no
field takes a user id, so no query can read another account's mail.
"""

import json
from typing import Any

from django.test import Client

from apps.notifications.models import Notification
from apps.notifications.tests.conftest import access_token

LIST = """
query($unread: Boolean) {
  notifications(unread: $unread) { id subject read }
  unreadNotificationCount
}
"""
READ_ALL = "mutation { readAllNotifications { count unread } }"
READ_ONE = "mutation($id: String!) { readNotification(notificationId: $id) { id unread } }"


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
    rows = graphql(LIST, alice)["data"]["notifications"]

    assert {row["subject"] for row in rows} == {announcement.subject, for_alice.subject}


def test_another_account_s_mail_is_not_visible(
    for_alice: Notification, for_bob: Notification, alice: Any
) -> None:
    subjects = {row["subject"] for row in graphql(LIST, alice)["data"]["notifications"]}

    assert for_bob.subject not in subjects


def test_marking_one_read_reports_the_badge_number_back(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    body = graphql(READ_ONE, alice, id=str(for_alice.pk))["data"]["readNotification"]

    assert body["id"] == str(for_alice.pk)
    assert body["unread"] == 1


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
