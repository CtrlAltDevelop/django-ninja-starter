"""Support over gRPC.

``transactional_db`` throughout: the server answers on its own connection and
cannot see rows still sitting in a test's open transaction.

The refusals are the interesting half. A service that speaks four protocols has
four chances to disagree with itself about who may do what, so these assert the
status codes as carefully as they assert the answers -- ``NOT_FOUND`` for
somebody else's conversation, ``PERMISSION_DENIED`` for the desk's own verbs,
``UNAUTHENTICATED`` for no credential at all.
"""

import json
from collections.abc import Callable
from typing import Any

import grpc
import pytest
from google.protobuf.empty_pb2 import Empty

from apps.support.grpc import support_pb2, support_pb2_grpc
from apps.support.models import Ticket, post_message

pytestmark = pytest.mark.django_db(transaction=True)

Stub = support_pb2_grpc.SupportControllerStub


@pytest.fixture
def token(client_user: Any) -> str:
    from apps.support.tests.conftest import access_token

    return access_token(client_user)


@pytest.fixture
def desk_token(agent: Any) -> str:
    from apps.support.tests.conftest import access_token

    return access_token(agent)


def refusal(call: Callable[..., Any], *args: Any, **kwargs: Any) -> grpc.StatusCode:
    with pytest.raises(grpc.aio.AioRpcError) as error:
        call(*args, **kwargs)
    return error.value.code()


# -- reading ----------------------------------------------------------------


def test_the_queue_needs_a_credential(grpc_call: Callable[..., Any]) -> None:
    assert (
        refusal(grpc_call, Stub, "List", support_pb2.ListRequest())
        is grpc.StatusCode.UNAUTHENTICATED
    )


def test_a_client_sees_their_own_conversations(
    ticket: Ticket, token: str, grpc_call: Callable[..., Any]
) -> None:
    answer = grpc_call(Stub, "List", support_pb2.ListRequest(), token=token)

    assert answer.total == 1
    assert answer.tickets[0].subject == "I was charged twice"
    assert answer.tickets[0].reference == ticket.reference
    assert answer.tickets[0].client.username == "clara"


def test_another_clients_conversation_is_not_in_the_list(
    ticket: Ticket, other_client: Any, grpc_call: Callable[..., Any]
) -> None:
    from apps.support.tests.conftest import access_token

    answer = grpc_call(Stub, "List", support_pb2.ListRequest(), token=access_token(other_client))

    assert answer.total == 0


def test_another_clients_conversation_cannot_be_fetched_by_id(
    ticket: Ticket, other_client: Any, grpc_call: Callable[..., Any]
) -> None:
    """NOT_FOUND rather than PERMISSION_DENIED: a stranger learns nothing."""
    from apps.support.tests.conftest import access_token

    code = refusal(
        grpc_call,
        Stub,
        "Get",
        support_pb2.GetRequest(ticket_id=str(ticket.id)),
        token=access_token(other_client),
    )

    assert code is grpc.StatusCode.NOT_FOUND


def test_the_count_answers_the_same_filters_as_the_list(
    ticket: Ticket, desk_token: str, grpc_call: Callable[..., Any]
) -> None:
    every = grpc_call(Stub, "Count", support_pb2.CountRequest(), token=desk_token)
    unassigned = grpc_call(
        Stub, "Count", support_pb2.CountRequest(unassigned=True), token=desk_token
    )
    claimed = grpc_call(Stub, "Count", support_pb2.CountRequest(status="closed"), token=desk_token)

    assert every.count == 1
    assert unassigned.count == 1
    assert claimed.count == 0


def test_one_conversation_carries_its_participants_and_promise(
    ticket: Ticket, token: str, category: Any, grpc_call: Callable[..., Any]
) -> None:
    one = grpc_call(Stub, "Get", support_pb2.GetRequest(ticket_id=str(ticket.id)), token=token)

    assert one.category.name == "Billing"
    assert one.sla.breached is False
    assert [row.user.username for row in one.participants] == ["clara"]


def test_a_ticket_id_that_is_not_an_id_is_refused(
    client_user: Any, token: str, grpc_call: Callable[..., Any]
) -> None:
    code = refusal(
        grpc_call, Stub, "Get", support_pb2.GetRequest(ticket_id="seventeen"), token=token
    )

    assert code is grpc.StatusCode.INVALID_ARGUMENT


def test_a_conversations_messages_come_back_oldest_first(
    ticket: Ticket, token: str, grpc_call: Callable[..., Any]
) -> None:
    grpc_call(
        Stub,
        "Send",
        support_pb2.SendRequest(ticket_id=str(ticket.id), body="And a third one now."),
        token=token,
    )

    page = grpc_call(
        Stub,
        "Messages",
        support_pb2.MessagesRequest(ticket_id=str(ticket.id)),
        token=token,
    )

    assert [row.body for row in page.messages] == [
        "There are two charges on the 3rd.",
        "And a third one now.",
    ]
    assert page.total == 2


def test_a_client_is_never_sent_an_internal_note(
    ticket: Ticket, agent: Any, token: str, desk_token: str, grpc_call: Callable[..., Any]
) -> None:
    grpc_call(
        Stub,
        "Send",
        support_pb2.SendRequest(ticket_id=str(ticket.id), body="Refund approved.", internal=True),
        token=desk_token,
    )

    hers = grpc_call(
        Stub, "Messages", support_pb2.MessagesRequest(ticket_id=str(ticket.id)), token=token
    )
    theirs = grpc_call(
        Stub,
        "Messages",
        support_pb2.MessagesRequest(ticket_id=str(ticket.id)),
        token=desk_token,
    )

    assert "Refund approved." not in [row.body for row in hers.messages]
    assert "Refund approved." in [row.body for row in theirs.messages]


def test_the_badge_counts_what_is_waiting(
    ticket: Ticket, agent: Any, token: str, grpc_call: Callable[..., Any]
) -> None:
    post_message(ticket, agent, "Looking into it.")

    badge = grpc_call(Stub, "Unread", Empty(), token=token)

    assert (badge.messages, badge.tickets) == (1, 1)


def test_the_categories_are_readable_by_any_signed_in_account(
    category: Any, token: str, grpc_call: Callable[..., Any]
) -> None:
    answer = grpc_call(Stub, "Categories", Empty(), token=token)

    assert [row.name for row in answer.categories] == ["Billing"]
    assert answer.categories[0].first_response_minutes == 60


def test_the_desks_tags_replies_and_numbers_are_staff_only(
    ticket: Ticket,
    tag: Any,
    canned: Any,
    token: str,
    desk_token: str,
    grpc_call: Callable[..., Any],
) -> None:
    tags = grpc_call(Stub, "Tags", Empty(), token=desk_token)
    replies = grpc_call(Stub, "Canned", support_pb2.CannedRequest(), token=desk_token)
    stats = grpc_call(Stub, "Stats", Empty(), token=desk_token)

    assert [row.name for row in tags.tags] == ["Escalated"]
    assert [row.title for row in replies.replies] == ["Asking for an invoice number"]
    assert stats.open == 1

    assert (
        refusal(grpc_call, Stub, "Stats", Empty(), token=token) is grpc.StatusCode.PERMISSION_DENIED
    )


# -- opening and talking ----------------------------------------------------


def test_a_client_can_open_a_ticket(
    category: Any, token: str, grpc_call: Callable[..., Any]
) -> None:
    opened = grpc_call(
        Stub,
        "Open",
        support_pb2.OpenRequest(
            subject="My invoice is wrong",
            body="The VAT line is doubled.",
            kind="ticket",
            category=category.slug,
            data_json=json.dumps({"invoice": "INV-9"}),
        ),
        token=token,
    )

    assert opened.subject == "My invoice is wrong"
    assert opened.reference.startswith("SUP-")
    assert json.loads(opened.data_json) == {"invoice": "INV-9"}


def test_a_ticket_with_no_subject_is_refused(
    client_user: Any, token: str, grpc_call: Callable[..., Any]
) -> None:
    code = refusal(
        grpc_call,
        Stub,
        "Open",
        support_pb2.OpenRequest(body="Something broke.", kind="ticket"),
        token=token,
    )

    assert code is grpc.StatusCode.INVALID_ARGUMENT


def test_data_that_is_not_json_is_refused(
    client_user: Any, token: str, grpc_call: Callable[..., Any]
) -> None:
    """The one thing the protobuf cannot type-check for us."""
    code = refusal(
        grpc_call,
        Stub,
        "Open",
        support_pb2.OpenRequest(subject="Hello", kind="ticket", data_json="{oh no"),
        token=token,
    )

    assert code is grpc.StatusCode.INVALID_ARGUMENT


def test_a_message_can_be_sent_edited_and_retracted(
    ticket: Ticket, token: str, grpc_call: Callable[..., Any]
) -> None:
    sent = grpc_call(
        Stub,
        "Send",
        support_pb2.SendRequest(ticket_id=str(ticket.id), body="Typo herr"),
        token=token,
    )
    edited = grpc_call(
        Stub,
        "Edit",
        support_pb2.EditRequest(message_id=sent.id, body="Typo here"),
        token=token,
    )
    deleted = grpc_call(Stub, "Delete", support_pb2.DeleteRequest(message_id=sent.id), token=token)

    assert sent.author.username == "clara"
    assert edited.body == "Typo here"
    assert deleted.deleted is True
    assert deleted.body == ""


def test_a_client_may_not_leave_an_internal_note(
    ticket: Ticket, token: str, grpc_call: Callable[..., Any]
) -> None:
    code = refusal(
        grpc_call,
        Stub,
        "Send",
        support_pb2.SendRequest(ticket_id=str(ticket.id), body="Sneaky.", internal=True),
        token=token,
    )

    assert code is grpc.StatusCode.PERMISSION_DENIED


def test_reading_clears_the_badge_and_unreading_puts_it_back(
    ticket: Ticket, agent: Any, token: str, grpc_call: Callable[..., Any]
) -> None:
    post_message(ticket, agent, "Looking into it.")

    read = grpc_call(Stub, "Read", support_pb2.ReadRequest(ticket_id=str(ticket.id)), token=token)
    after = grpc_call(Stub, "Unread", Empty(), token=token)
    back = grpc_call(
        Stub,
        "UnreadTicket",
        support_pb2.UnreadTicketRequest(ticket_id=str(ticket.id)),
        token=token,
    )

    assert (read.changed, read.unread) == (True, 0)
    assert after.messages == 0
    assert back.unread == 1


def test_typing_is_acknowledged(ticket: Ticket, token: str, grpc_call: Callable[..., Any]) -> None:
    answer = grpc_call(
        Stub,
        "Typing",
        support_pb2.TypingRequest(ticket_id=str(ticket.id), typing=True),
        token=token,
    )

    assert (answer.ticket, answer.typing) == (str(ticket.id), True)


# -- either side ------------------------------------------------------------


def test_a_client_can_close_rate_and_reopen_their_own_conversation(
    ticket: Ticket, token: str, grpc_call: Callable[..., Any]
) -> None:
    closed = grpc_call(
        Stub,
        "Status",
        support_pb2.StatusRequest(ticket_id=str(ticket.id), status="closed"),
        token=token,
    )
    rated = grpc_call(
        Stub,
        "Rate",
        support_pb2.RateRequest(ticket_id=str(ticket.id), score=5, comment="Quick."),
        token=token,
    )
    reopened = grpc_call(
        Stub,
        "Status",
        support_pb2.StatusRequest(ticket_id=str(ticket.id), status="open"),
        token=token,
    )

    assert closed.status == "closed"
    assert (rated.rating, rated.comment) == (5, "Quick.")
    assert reopened.status == "open"


def test_a_client_may_not_set_a_status_that_is_the_desks_to_set(
    ticket: Ticket, token: str, grpc_call: Callable[..., Any]
) -> None:
    code = refusal(
        grpc_call,
        Stub,
        "Status",
        support_pb2.StatusRequest(ticket_id=str(ticket.id), status="pending"),
        token=token,
    )

    assert code is grpc.StatusCode.PERMISSION_DENIED


def test_a_score_outside_the_scale_is_refused(
    ticket: Ticket, token: str, grpc_call: Callable[..., Any]
) -> None:
    grpc_call(
        Stub,
        "Status",
        support_pb2.StatusRequest(ticket_id=str(ticket.id), status="closed"),
        token=token,
    )

    code = refusal(
        grpc_call,
        Stub,
        "Rate",
        support_pb2.RateRequest(ticket_id=str(ticket.id), score=9),
        token=token,
    )

    assert code is grpc.StatusCode.INVALID_ARGUMENT


# -- the desk's own ---------------------------------------------------------


def test_an_agent_can_claim_assign_prioritise_and_tag(
    ticket: Ticket,
    second_agent: Any,
    tag: Any,
    desk_token: str,
    grpc_call: Callable[..., Any],
) -> None:
    claimed = grpc_call(
        Stub, "Claim", support_pb2.ClaimRequest(ticket_id=str(ticket.id)), token=desk_token
    )
    assigned = grpc_call(
        Stub,
        "Assign",
        support_pb2.AssignRequest(ticket_id=str(ticket.id), agent=str(second_agent.pk)),
        token=desk_token,
    )
    raised = grpc_call(
        Stub,
        "Priority",
        support_pb2.PriorityRequest(ticket_id=str(ticket.id), priority="urgent"),
        token=desk_token,
    )
    tagged = grpc_call(
        Stub,
        "Tag",
        support_pb2.TagRequest(ticket_id=str(ticket.id), tags=[tag.slug]),
        token=desk_token,
    )

    assert claimed.assignee.username == "agatha"
    assert assigned.assignee.username == "alan"
    assert raised.priority == "urgent"
    assert list(tagged.tags) == [tag.slug]


def test_a_tag_that_does_not_exist_is_refused(
    ticket: Ticket, desk_token: str, grpc_call: Callable[..., Any]
) -> None:
    code = refusal(
        grpc_call,
        Stub,
        "Tag",
        support_pb2.TagRequest(ticket_id=str(ticket.id), tags=["invented"]),
        token=desk_token,
    )

    assert code is grpc.StatusCode.INVALID_ARGUMENT


def test_a_client_may_not_run_the_desk(
    ticket: Ticket, token: str, grpc_call: Callable[..., Any]
) -> None:
    code = refusal(
        grpc_call,
        Stub,
        "Claim",
        support_pb2.ClaimRequest(ticket_id=str(ticket.id)),
        token=token,
    )

    assert code is grpc.StatusCode.PERMISSION_DENIED


def test_an_agent_can_invite_somebody_into_a_conversation(
    ticket: Ticket, second_agent: Any, desk_token: str, grpc_call: Callable[..., Any]
) -> None:
    invited = grpc_call(
        Stub,
        "Invite",
        support_pb2.InviteRequest(
            ticket_id=str(ticket.id), account=str(second_agent.pk), role="observer"
        ),
        token=desk_token,
    )

    assert invited.user.username == "alan"
    assert invited.role == "observer"
