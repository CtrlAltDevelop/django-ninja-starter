"""Support over GraphQL.

The same credential the socket and the routes accept, and the same scoping: no
field takes an account id, so no query reads a conversation the caller is not
in. The refusals carry the same titles the REST router answers with, which is
the point of routing every transport through one service.
"""

import json
from typing import Any

import pytest
from django.test import Client

from apps.support.models import Ticket, post_message
from apps.support.tests.conftest import access_token

pytestmark = pytest.mark.django_db

TICKETS = """
query($status: String, $kind: String, $mine: Boolean! = false,
      $unassigned: Boolean! = false, $search: String, $limit: Int! = 50) {
  supportTickets(status: $status, kind: $kind, mine: $mine, unassigned: $unassigned,
                 search: $search, limit: $limit) {
    tickets { id reference subject status priority kind unread tags }
    total
    limit
    offset
  }
  supportUnread { messages tickets }
}
"""
TICKET = """
query($id: String!) {
  supportTicket(ticketId: $id) {
    id subject status unread
    client { username staff }
    assignee { username }
    category { name }
    sla { breached firstResponseBreached }
    participants { role notify user { username } }
  }
}
"""
MESSAGES = """
query($id: String!) {
  supportMessages(ticketId: $id) {
    ticket
    messages { id body kind visibility deleted author { username } }
    total
  }
}
"""
CATEGORIES = "query { supportCategories { name slug firstResponseMinutes } }"
TAGS = "query { supportTags { name slug colour } }"
CANNED = "query { supportCannedReplies { title body } }"
STATS = "query { supportStats { total open unassigned satisfaction } }"

OPEN = """
mutation($subject: String!, $body: String!, $kind: String!, $category: String) {
  openSupportTicket(subject: $subject, body: $body, kind: $kind, category: $category) {
    id reference subject kind status priority
  }
}
"""
SEND = """
mutation($id: String!, $body: String!, $internal: Boolean! = false) {
  sendSupportMessage(ticketId: $id, body: $body, internal: $internal) {
    id body visibility author { username }
  }
}
"""
EDIT = """
mutation($id: String!, $body: String!) {
  editSupportMessage(messageId: $id, body: $body) { id body }
}
"""
DELETE = "mutation($id: String!) { deleteSupportMessage(messageId: $id) { id deleted body } }"
READ = "mutation($id: String!) { readSupportTicket(ticketId: $id) { ticket changed unread } }"
UNREAD = "mutation($id: String!) { unreadSupportTicket(ticketId: $id) { ticket changed unread } }"
STATUS = """
mutation($id: String!, $status: String!) {
  setSupportStatus(ticketId: $id, status: $status) { ticket status changed }
}
"""
CLOSE = "mutation($id: String!) { closeSupportTicket(ticketId: $id) { ticket status } }"
REOPEN = "mutation($id: String!) { reopenSupportTicket(ticketId: $id) { ticket status } }"
CLAIM = """
mutation($id: String!) {
  claimSupportTicket(ticketId: $id) { ticket changed assignee { username } }
}
"""
ASSIGN = """
mutation($id: String!, $agent: String) {
  assignSupportTicket(ticketId: $id, agent: $agent) { ticket assignee { username } }
}
"""
PRIORITY = """
mutation($id: String!, $priority: String!) {
  setSupportPriority(ticketId: $id, priority: $priority) { ticket priority changed }
}
"""
TAG = """
mutation($id: String!, $tags: [String!]!) {
  tagSupportTicket(ticketId: $id, tags: $tags) { ticket tags changed }
}
"""
INVITE = """
mutation($id: String!, $account: String!, $role: String!) {
  inviteToSupportTicket(ticketId: $id, account: $account, role: $role) {
    role notify user { username }
  }
}
"""
RATE = """
mutation($id: String!, $score: Int!, $comment: String!) {
  rateSupportTicket(ticketId: $id, score: $score, comment: $comment) {
    ticket rating comment
  }
}
"""
TYPING = "mutation($id: String!) { supportTyping(ticketId: $id) { ticket typing } }"


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


def title(body: dict[str, Any]) -> str:
    return str(body["errors"][0]["extensions"]["title"])


# -- reading ----------------------------------------------------------------


def test_the_queue_needs_a_credential(db: None) -> None:
    assert title(graphql(TICKETS)) == "AUTHENTICATION_REQUIRED"


def test_a_client_sees_their_own_conversations(client_user: Any, ticket: Ticket) -> None:
    body = graphql(TICKETS, client_user)["data"]
    page = body["supportTickets"]

    assert page["total"] == 1
    assert page["tickets"][0]["subject"] == "I was charged twice"
    assert page["tickets"][0]["reference"] == ticket.reference
    assert body["supportUnread"] == {"messages": 0, "tickets": 0}


def test_another_clients_conversation_is_not_in_the_list(other_client: Any, ticket: Ticket) -> None:
    page = graphql(TICKETS, other_client)["data"]["supportTickets"]

    assert page["tickets"] == []
    assert page["total"] == 0


def test_another_clients_conversation_cannot_be_fetched_by_id(
    other_client: Any, ticket: Ticket
) -> None:
    """Not FORBIDDEN: a stranger should not learn that the id exists."""
    assert title(graphql(TICKET, other_client, id=str(ticket.id))) == "NOT_FOUND"


def test_the_desk_sees_every_conversation(agent: Any, ticket: Ticket) -> None:
    page = graphql(TICKETS, agent)["data"]["supportTickets"]

    assert page["total"] == 1


def test_the_queue_can_be_filtered_down_to_the_unassigned(agent: Any, ticket: Ticket) -> None:
    assert graphql(TICKETS, agent, unassigned=True)["data"]["supportTickets"]["total"] == 1

    graphql(CLAIM, agent, id=str(ticket.id))

    assert graphql(TICKETS, agent, unassigned=True)["data"]["supportTickets"]["total"] == 0


def test_the_queue_can_be_searched(client_user: Any, ticket: Ticket) -> None:
    found = graphql(TICKETS, client_user, search="charged")["data"]["supportTickets"]
    missed = graphql(TICKETS, client_user, search="kumquat")["data"]["supportTickets"]

    assert found["total"] == 1
    assert missed["total"] == 0


def test_one_conversation_carries_who_is_in_it(
    client_user: Any, ticket: Ticket, category: Any
) -> None:
    one = graphql(TICKET, client_user, id=str(ticket.id))["data"]["supportTicket"]

    assert one["client"] == {"username": "clara", "staff": False}
    assert one["assignee"] is None
    assert one["category"]["name"] == "Billing"
    assert one["sla"]["breached"] is False
    assert [row["user"]["username"] for row in one["participants"]] == ["clara"]


def test_a_conversations_messages_are_oldest_first(client_user: Any, ticket: Ticket) -> None:
    graphql(SEND, client_user, id=str(ticket.id), body="And a third one now.")

    page = graphql(MESSAGES, client_user, id=str(ticket.id))["data"]["supportMessages"]

    assert [row["body"] for row in page["messages"]] == [
        "There are two charges on the 3rd.",
        "And a third one now.",
    ]
    assert page["total"] == 2


def test_a_client_is_never_shown_an_internal_note(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    graphql(SEND, agent, id=str(ticket.id), body="Refund approved.", internal=True)

    hers = graphql(MESSAGES, client_user, id=str(ticket.id))["data"]["supportMessages"]
    theirs = graphql(MESSAGES, agent, id=str(ticket.id))["data"]["supportMessages"]

    assert "Refund approved." not in [row["body"] for row in hers["messages"]]
    assert "Refund approved." in [row["body"] for row in theirs["messages"]]


def test_the_categories_a_ticket_can_be_filed_under_are_readable(
    client_user: Any, category: Any
) -> None:
    rows = graphql(CATEGORIES, client_user)["data"]["supportCategories"]

    assert rows[0]["name"] == "Billing"
    assert rows[0]["firstResponseMinutes"] == 60


def test_the_desks_tags_replies_and_numbers_are_staff_only(
    client_user: Any, agent: Any, tag: Any, canned: Any, ticket: Ticket
) -> None:
    assert graphql(TAGS, agent)["data"]["supportTags"][0]["name"] == "Escalated"
    assert graphql(CANNED, agent)["data"]["supportCannedReplies"][0]["title"] == (
        "Asking for an invoice number"
    )
    assert graphql(STATS, agent)["data"]["supportStats"]["open"] == 1

    assert title(graphql(TAGS, client_user)) == "FORBIDDEN"
    assert title(graphql(STATS, client_user)) == "FORBIDDEN"


# -- opening and talking ----------------------------------------------------


def test_a_client_can_open_a_ticket(client_user: Any, category: Any) -> None:
    opened = graphql(
        OPEN,
        client_user,
        subject="My invoice is wrong",
        body="The VAT line is doubled.",
        kind="ticket",
        category=category.slug,
    )["data"]["openSupportTicket"]

    assert opened["subject"] == "My invoice is wrong"
    assert opened["status"] == "open"
    assert opened["reference"].startswith("SUP-")


def test_a_chat_needs_no_subject(client_user: Any) -> None:
    opened = graphql(OPEN, client_user, subject="", body="Anyone there?", kind="chat")

    assert opened["data"]["openSupportTicket"]["kind"] == "chat"


def test_a_ticket_with_no_subject_is_refused(client_user: Any) -> None:
    body = graphql(OPEN, client_user, subject="", body="Something broke.", kind="ticket")

    assert title(body) == "BAD_REQUEST"


def test_a_message_can_be_sent_edited_and_retracted(client_user: Any, ticket: Ticket) -> None:
    sent = graphql(SEND, client_user, id=str(ticket.id), body="Typo herr")["data"][
        "sendSupportMessage"
    ]
    edited = graphql(EDIT, client_user, id=sent["id"], body="Typo here")["data"][
        "editSupportMessage"
    ]
    deleted = graphql(DELETE, client_user, id=sent["id"])["data"]["deleteSupportMessage"]

    assert sent["author"]["username"] == "clara"
    assert edited["body"] == "Typo here"
    assert deleted["deleted"] is True
    assert deleted["body"] == ""


def test_a_client_may_not_leave_an_internal_note(client_user: Any, ticket: Ticket) -> None:
    body = graphql(SEND, client_user, id=str(ticket.id), body="Sneaky.", internal=True)

    assert title(body) == "FORBIDDEN"


def test_a_client_may_not_edit_somebody_elses_message(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    theirs = graphql(SEND, agent, id=str(ticket.id), body="Looking into it.")["data"][
        "sendSupportMessage"
    ]

    body = graphql(EDIT, client_user, id=theirs["id"], body="No you are not.")

    assert title(body) == "FORBIDDEN"


def test_an_id_that_is_not_an_id_is_a_bad_request(client_user: Any) -> None:
    assert title(graphql(TICKET, client_user, id="seventeen")) == "BAD_REQUEST"


def test_reading_clears_the_badge_and_unreading_puts_it_back(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    post_message(ticket, agent, "Looking into it.")

    before = graphql(TICKETS, client_user)["data"]["supportUnread"]
    read = graphql(READ, client_user, id=str(ticket.id))["data"]["readSupportTicket"]
    after = graphql(TICKETS, client_user)["data"]["supportUnread"]
    unread = graphql(UNREAD, client_user, id=str(ticket.id))["data"]["unreadSupportTicket"]

    assert before == {"messages": 1, "tickets": 1}
    assert read == {"ticket": str(ticket.id), "changed": True, "unread": 0}
    assert after == {"messages": 0, "tickets": 0}
    assert unread["unread"] == 1


def test_reading_twice_reports_the_second_call_changed_nothing(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    """What lets a scroll handler fire this as often as it likes."""
    post_message(ticket, agent, "Looking into it.")
    graphql(READ, client_user, id=str(ticket.id))

    again = graphql(READ, client_user, id=str(ticket.id))["data"]["readSupportTicket"]

    assert again["changed"] is False


def test_typing_is_acknowledged(client_user: Any, ticket: Ticket) -> None:
    body = graphql(TYPING, client_user, id=str(ticket.id))["data"]["supportTyping"]

    assert body == {"ticket": str(ticket.id), "typing": True}


# -- either side ------------------------------------------------------------


def test_a_client_can_close_reopen_and_rate_their_own_conversation(
    client_user: Any, ticket: Ticket
) -> None:
    closed = graphql(CLOSE, client_user, id=str(ticket.id))["data"]["closeSupportTicket"]
    rated = graphql(RATE, client_user, id=str(ticket.id), score=5, comment="Quick.")["data"][
        "rateSupportTicket"
    ]
    reopened = graphql(REOPEN, client_user, id=str(ticket.id))["data"]["reopenSupportTicket"]

    assert closed["status"] == "closed"
    assert rated["rating"] == 5
    assert rated["comment"] == "Quick."
    assert reopened["status"] == "open"


def test_a_client_may_not_put_a_ticket_into_a_status_that_is_the_desks_to_set(
    client_user: Any, ticket: Ticket
) -> None:
    """`pending` is a statement about what the desk is waiting for."""
    assert title(graphql(STATUS, client_user, id=str(ticket.id), status="pending")) == ("FORBIDDEN")


def test_a_status_that_does_not_exist_is_a_bad_request(agent: Any, ticket: Ticket) -> None:
    assert title(graphql(STATUS, agent, id=str(ticket.id), status="haunted")) == ("BAD_REQUEST")


def test_only_the_client_rates_and_only_a_settled_conversation(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    assert title(graphql(RATE, client_user, id=str(ticket.id), score=5, comment="")) == (
        "FORBIDDEN"
    )

    graphql(CLOSE, client_user, id=str(ticket.id))

    assert title(graphql(RATE, agent, id=str(ticket.id), score=5, comment="")) == ("FORBIDDEN")


def test_a_score_outside_the_scale_is_refused(client_user: Any, ticket: Ticket) -> None:
    graphql(CLOSE, client_user, id=str(ticket.id))

    assert title(graphql(RATE, client_user, id=str(ticket.id), score=9, comment="")) == (
        "BAD_REQUEST"
    )


# -- the desk's own ---------------------------------------------------------


def test_an_agent_can_claim_assign_prioritise_and_tag(
    agent: Any, second_agent: Any, ticket: Ticket, tag: Any
) -> None:
    claimed = graphql(CLAIM, agent, id=str(ticket.id))["data"]["claimSupportTicket"]
    assigned = graphql(ASSIGN, agent, id=str(ticket.id), agent=str(second_agent.pk))["data"][
        "assignSupportTicket"
    ]
    raised = graphql(PRIORITY, agent, id=str(ticket.id), priority="urgent")["data"][
        "setSupportPriority"
    ]
    tagged = graphql(TAG, agent, id=str(ticket.id), tags=[tag.slug])["data"]["tagSupportTicket"]

    assert claimed["assignee"]["username"] == "agatha"
    assert assigned["assignee"]["username"] == "alan"
    assert raised["priority"] == "urgent"
    assert tagged["tags"] == [tag.slug]


def test_a_tag_that_does_not_exist_is_refused(agent: Any, ticket: Ticket) -> None:
    assert title(graphql(TAG, agent, id=str(ticket.id), tags=["invented"])) == ("BAD_REQUEST")


def test_a_client_may_not_run_the_desk(client_user: Any, ticket: Ticket) -> None:
    assert title(graphql(CLAIM, client_user, id=str(ticket.id))) == "FORBIDDEN"
    assert title(graphql(PRIORITY, client_user, id=str(ticket.id), priority="urgent")) == (
        "FORBIDDEN"
    )


def test_an_agent_can_invite_somebody_into_a_conversation(
    agent: Any, second_agent: Any, ticket: Ticket
) -> None:
    invited = graphql(
        INVITE, agent, id=str(ticket.id), account=str(second_agent.pk), role="observer"
    )["data"]["inviteToSupportTicket"]

    assert invited["user"]["username"] == "alan"
    assert invited["role"] == "observer"


# -- rooms ------------------------------------------------------------------

CREATE_CHANNEL = """
mutation($name: String!, $body: String! = "") {
  createSupportChannel(name: $name, body: $body) { id reference kind subject slug }
}
"""
CHANNELS = """
query($search: String! = "") {
  supportChannels(search: $search) { id subject slug joined members }
}
"""
JOIN = """
mutation($id: String!) { joinSupportRoom(ticketId: $id) { role } }
"""
LEAVE = """
mutation($id: String!) { leaveSupportRoom(ticketId: $id) { ticket left } }
"""
CREATE_GROUP = """
mutation($name: String!, $members: [String!]!) {
  createSupportGroup(name: $name, members: $members) { id kind subject }
}
"""
DIRECT = """
mutation($account: String!) { openSupportDirect(account: $account) { id kind reference } }
"""


def test_a_channel_is_opened_and_carries_its_address(client_user: Any) -> None:
    body = graphql(CREATE_CHANNEL, client_user, name="Release notes")

    channel = body["data"]["createSupportChannel"]
    assert channel["kind"] == "channel"
    assert channel["slug"] == "release-notes"


def test_the_channel_directory_says_whether_you_are_in_one(
    client_user: Any, other_client: Any
) -> None:
    graphql(CREATE_CHANNEL, client_user, name="Release notes")

    listing = graphql(CHANNELS, other_client)["data"]["supportChannels"]

    assert listing[0]["joined"] is False
    assert listing[0]["members"] == 1


def test_a_channel_is_joined_and_left_over_graphql(client_user: Any, other_client: Any) -> None:
    channel = graphql(CREATE_CHANNEL, client_user, name="Release notes")["data"][
        "createSupportChannel"
    ]

    joined = graphql(JOIN, other_client, id=channel["id"])["data"]["joinSupportRoom"]
    left = graphql(LEAVE, other_client, id=channel["id"])["data"]["leaveSupportRoom"]

    assert joined["role"] == "member"
    assert left["left"] is True


def test_a_group_over_graphql_is_hidden_from_the_desk(
    client_user: Any, other_client: Any, agent: Any
) -> None:
    group = graphql(CREATE_GROUP, client_user, name="Ours", members=[str(other_client.pk)])["data"][
        "createSupportGroup"
    ]

    body = graphql(TICKET, agent, id=group["id"])

    assert title(body) == "NOT_FOUND"


def test_a_private_chat_over_graphql_is_the_same_thread_twice(
    client_user: Any, other_client: Any
) -> None:
    first = graphql(DIRECT, client_user, account=str(other_client.pk))["data"]["openSupportDirect"]
    second = graphql(DIRECT, other_client, account=str(client_user.pk))["data"]["openSupportDirect"]

    assert first["id"] == second["id"]
