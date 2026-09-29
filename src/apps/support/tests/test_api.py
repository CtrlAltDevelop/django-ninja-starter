"""The HTTP half: a client's conversations, and the desk's queue, over one router.

Every test here goes through a real bearer token from the project's own issuer,
because "the socket accepts this credential and the API does not" is precisely
the kind of drift these endpoints are worth protecting against.

The point of most of these is not the payload but the *status*: the same URL
answers 200 for an agent and 403 for a client, or 404 for a client who is not
this one -- and getting that mapping wrong is the whole risk in publishing a
support desk.
"""

import json
from typing import Any
from uuid import uuid4

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from apps.support.models import MessageKind, Status, Ticket, Upload, post_message
from apps.support.tests.conftest import auth

SUPPORT = "/api/v1/support"
MISSING = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def http() -> Client:
    return Client()


def _data(response: Any) -> Any:
    """The payload inside this project's response envelope."""
    return response.json()["data"]


def _post(client: Client, user: Any, path: str, payload: Any = None) -> Any:
    return client.post(
        path, data=json.dumps(payload or {}), content_type="application/json", **auth(user)
    )


# -- authentication ---------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", SUPPORT),
        ("post", SUPPORT),
        ("get", f"{SUPPORT}/unread"),
        ("get", f"{SUPPORT}/categories"),
        ("get", f"{SUPPORT}/tags"),
        ("get", f"{SUPPORT}/canned-replies"),
        ("get", f"{SUPPORT}/stats"),
        ("post", f"{SUPPORT}/uploads"),
        ("get", f"{SUPPORT}/{MISSING}"),
        ("get", f"{SUPPORT}/{MISSING}/messages"),
        ("post", f"{SUPPORT}/{MISSING}/messages"),
        ("post", f"{SUPPORT}/{MISSING}/notes"),
        ("post", f"{SUPPORT}/{MISSING}/read"),
        ("post", f"{SUPPORT}/{MISSING}/unread"),
        ("post", f"{SUPPORT}/{MISSING}/status"),
        ("post", f"{SUPPORT}/{MISSING}/close"),
        ("post", f"{SUPPORT}/{MISSING}/reopen"),
        ("post", f"{SUPPORT}/{MISSING}/assign"),
        ("post", f"{SUPPORT}/{MISSING}/claim"),
        ("post", f"{SUPPORT}/{MISSING}/priority"),
        ("post", f"{SUPPORT}/{MISSING}/tags"),
        ("post", f"{SUPPORT}/{MISSING}/participants"),
        ("post", f"{SUPPORT}/{MISSING}/rating"),
        ("post", f"{SUPPORT}/{MISSING}/typing"),
        ("patch", f"{SUPPORT}/messages/{MISSING}"),
        ("delete", f"{SUPPORT}/messages/{MISSING}"),
    ],
)
def test_every_endpoint_needs_a_credential(http: Client, method: str, path: str, db: None) -> None:
    """There is no public support traffic, so there is no public endpoint."""
    assert getattr(http, method)(path).status_code == 401


# -- opening ----------------------------------------------------------------


@pytest.mark.django_db
def test_a_client_opens_a_ticket(http: Client, client_user: Any, category: Any) -> None:
    response = _post(
        http,
        client_user,
        SUPPORT,
        {"subject": "I was charged twice", "body": "Two charges.", "category": "billing"},
    )

    assert response.status_code == 201
    payload = _data(response)
    assert payload["reference"].startswith("SUP-")
    assert payload["status"] == Status.OPEN
    assert payload["category"]["slug"] == "billing"
    assert payload["sla"]["first_response_due_at"] is not None


@pytest.mark.django_db
def test_a_ticket_without_a_body_is_a_400(http: Client, client_user: Any) -> None:
    response = _post(http, client_user, SUPPORT, {"subject": "Nothing to say"})

    assert response.status_code == 400


@pytest.mark.django_db
def test_a_chat_needs_nothing(http: Client, client_user: Any) -> None:
    response = _post(http, client_user, SUPPORT, {"kind": "chat"})

    assert response.status_code == 201
    assert _data(response)["kind"] == "chat"


@pytest.mark.django_db
def test_an_unknown_kind_is_a_validation_error(http: Client, client_user: Any) -> None:
    """Refused by the schema rather than by the service -- `kind` is an enum here."""
    assert _post(http, client_user, SUPPORT, {"kind": "urgent"}).status_code == 422


# -- listing ----------------------------------------------------------------


@pytest.mark.django_db
def test_a_client_lists_only_their_own(
    http: Client, client_user: Any, other_client: Any, ticket: Ticket
) -> None:
    _post(http, other_client, SUPPORT, {"subject": "Theirs", "body": "Theirs"})

    payload = _data(http.get(SUPPORT, **auth(client_user)))

    assert payload["total"] == 1
    assert payload["tickets"][0]["reference"] == ticket.reference


@pytest.mark.django_db
def test_staff_list_the_whole_desk(
    http: Client, agent: Any, client_user: Any, other_client: Any, ticket: Ticket
) -> None:
    _post(http, other_client, SUPPORT, {"subject": "Theirs", "body": "Theirs"})

    assert _data(http.get(SUPPORT, **auth(agent)))["total"] == 2


@pytest.mark.django_db
def test_the_unassigned_queue_is_staff_only(
    http: Client, client_user: Any, agent: Any, ticket: Ticket
) -> None:
    assert http.get(f"{SUPPORT}?unassigned=true", **auth(client_user)).status_code == 403
    assert http.get(f"{SUPPORT}?unassigned=true", **auth(agent)).status_code == 200


@pytest.mark.django_db
def test_search_over_http(http: Client, agent: Any, ticket: Ticket) -> None:
    found = _data(http.get(f"{SUPPORT}?search=charged+twice", **auth(agent)))
    missing = _data(http.get(f"{SUPPORT}?search=unrelated", **auth(agent)))

    assert found["total"] == 1
    assert missing["total"] == 0


@pytest.mark.django_db
def test_the_page_reports_its_own_bounds(http: Client, agent: Any, client_user: Any) -> None:
    for index in range(3):
        _post(http, client_user, SUPPORT, {"subject": f"#{index}", "body": "Body"})

    payload = _data(http.get(f"{SUPPORT}?limit=2&offset=1", **auth(agent)))

    assert (payload["limit"], payload["offset"], payload["total"]) == (2, 1, 3)
    assert len(payload["tickets"]) == 2


# -- reading one ------------------------------------------------------------


@pytest.mark.django_db
def test_another_clients_ticket_is_a_404(http: Client, other_client: Any, ticket: Ticket) -> None:
    """Not a 403: saying "forbidden" would confirm the ticket exists."""
    assert http.get(f"{SUPPORT}/{ticket.pk}", **auth(other_client)).status_code == 404


@pytest.mark.django_db
def test_a_ticket_that_does_not_exist_is_the_same_404(http: Client, client_user: Any) -> None:
    assert http.get(f"{SUPPORT}/{uuid4()}", **auth(client_user)).status_code == 404


@pytest.mark.django_db
def test_the_client_does_not_receive_the_internal_notes(
    http: Client, client_user: Any, agent: Any, ticket: Ticket
) -> None:
    post_message(ticket, agent, "Probably a duplicate", kind=str(MessageKind.NOTE))
    post_message(ticket, agent, "We are looking into it.")

    client_sees = _data(http.get(f"{SUPPORT}/{ticket.pk}/messages", **auth(client_user)))
    desk_sees = _data(http.get(f"{SUPPORT}/{ticket.pk}/messages", **auth(agent)))

    assert [message["body"] for message in client_sees["messages"]] == [
        "There are two charges on the 3rd.",
        "We are looking into it.",
    ]
    assert client_sees["total"] == 2
    assert desk_sees["total"] == 3


@pytest.mark.django_db
def test_messages_come_back_oldest_first(
    http: Client, client_user: Any, agent: Any, ticket: Ticket
) -> None:
    """A conversation is read forwards -- the one list in this project that is."""
    post_message(ticket, agent, "Second")
    post_message(ticket, client_user, "Third")

    messages = _data(http.get(f"{SUPPORT}/{ticket.pk}/messages", **auth(client_user)))["messages"]

    assert [message["body"] for message in messages] == [
        "There are two charges on the 3rd.",
        "Second",
        "Third",
    ]


# -- talking ----------------------------------------------------------------


@pytest.mark.django_db
def test_a_client_replies(http: Client, client_user: Any, ticket: Ticket) -> None:
    response = _post(http, client_user, f"{SUPPORT}/{ticket.pk}/messages", {"body": "Any news?"})

    assert response.status_code == 201
    assert _data(response)["author"]["username"] == "clara"


@pytest.mark.django_db
def test_a_client_cannot_post_a_note(http: Client, client_user: Any, ticket: Ticket) -> None:
    response = _post(http, client_user, f"{SUPPORT}/{ticket.pk}/notes", {"body": "Sneaky"})

    assert response.status_code == 403


@pytest.mark.django_db
def test_an_agent_posts_a_note(http: Client, agent: Any, ticket: Ticket) -> None:
    response = _post(http, agent, f"{SUPPORT}/{ticket.pk}/notes", {"body": "Duplicate"})

    assert response.status_code == 201
    assert _data(response)["visibility"] == "internal"


@pytest.mark.django_db
def test_an_empty_message_is_a_400(http: Client, client_user: Any, ticket: Ticket) -> None:
    assert (
        _post(http, client_user, f"{SUPPORT}/{ticket.pk}/messages", {"body": "  "}).status_code
        == 400
    )


@pytest.mark.django_db
def test_editing_and_retracting_over_http(
    http: Client, client_user: Any, agent: Any, ticket: Ticket
) -> None:
    message = _data(_post(http, client_user, f"{SUPPORT}/{ticket.pk}/messages", {"body": "Frist"}))

    edited = http.patch(
        f"{SUPPORT}/messages/{message['id']}",
        data=json.dumps({"body": "First"}),
        content_type="application/json",
        **auth(client_user),
    )
    assert _data(edited)["body"] == "First"

    deleted = http.delete(f"{SUPPORT}/messages/{message['id']}", **auth(agent))
    assert _data(deleted)["deleted"] is True
    assert _data(deleted)["body"] == ""


@pytest.mark.django_db
def test_editing_somebody_elses_message_is_a_403(
    http: Client, client_user: Any, agent: Any, ticket: Ticket
) -> None:
    message = _data(_post(http, agent, f"{SUPPORT}/{ticket.pk}/messages", {"body": "Ours"}))

    response = http.patch(
        f"{SUPPORT}/messages/{message['id']}",
        data=json.dumps({"body": "Theirs"}),
        content_type="application/json",
        **auth(client_user),
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_a_note_a_client_names_is_a_404_not_a_403(
    http: Client, client_user: Any, agent: Any, ticket: Ticket
) -> None:
    """They may not know it exists, so they are not told that it does."""
    note = post_message(ticket, agent, "Internal", kind=str(MessageKind.NOTE))

    response = http.delete(f"{SUPPORT}/messages/{note.pk}", **auth(client_user))

    assert response.status_code == 404


# -- read state -------------------------------------------------------------


@pytest.mark.django_db
def test_reading_and_unreading_over_http(
    http: Client, client_user: Any, agent: Any, ticket: Ticket
) -> None:
    _post(http, agent, f"{SUPPORT}/{ticket.pk}/messages", {"body": "Answer."})

    read = _data(_post(http, client_user, f"{SUPPORT}/{ticket.pk}/read"))
    assert (read["changed"], read["unread"]) == (True, 0)

    again = _data(_post(http, client_user, f"{SUPPORT}/{ticket.pk}/read"))
    assert again["changed"] is False

    back = _data(_post(http, client_user, f"{SUPPORT}/{ticket.pk}/unread"))
    assert (back["changed"], back["unread"], back["last_read_at"]) == (True, 1, None)


@pytest.mark.django_db
def test_the_badge_endpoint(http: Client, client_user: Any, agent: Any, ticket: Ticket) -> None:
    _post(http, agent, f"{SUPPORT}/{ticket.pk}/messages", {"body": "Answer."})

    assert _data(http.get(f"{SUPPORT}/unread", **auth(client_user))) == {
        "messages": 1,
        "tickets": 1,
    }


# -- status, assignment, priority -------------------------------------------


@pytest.mark.django_db
def test_a_client_closes_and_reopens_their_own(
    http: Client, client_user: Any, ticket: Ticket
) -> None:
    assert _data(_post(http, client_user, f"{SUPPORT}/{ticket.pk}/close"))["status"] == "closed"
    assert _data(_post(http, client_user, f"{SUPPORT}/{ticket.pk}/reopen"))["status"] == "open"


@pytest.mark.django_db
def test_a_client_cannot_set_a_desk_status(http: Client, client_user: Any, ticket: Ticket) -> None:
    response = _post(http, client_user, f"{SUPPORT}/{ticket.pk}/status", {"status": "pending"})

    assert response.status_code == 403


@pytest.mark.django_db
def test_assignment_and_claiming_are_staff_only(
    http: Client, client_user: Any, agent: Any, ticket: Ticket
) -> None:
    assert _post(http, client_user, f"{SUPPORT}/{ticket.pk}/claim").status_code == 403

    claimed = _post(http, agent, f"{SUPPORT}/{ticket.pk}/claim")
    assert _data(claimed)["assignee"]["username"] == "agatha"

    unassigned = _post(http, agent, f"{SUPPORT}/{ticket.pk}/assign", {"agent": None})
    assert _data(unassigned)["assignee"] is None


@pytest.mark.django_db
def test_a_second_agent_cannot_take_a_claimed_ticket(
    http: Client, agent: Any, second_agent: Any, ticket: Ticket
) -> None:
    _post(http, agent, f"{SUPPORT}/{ticket.pk}/claim")

    assert _post(http, second_agent, f"{SUPPORT}/{ticket.pk}/claim").status_code == 403


@pytest.mark.django_db
def test_priority_and_tags_are_staff_only(
    http: Client, client_user: Any, agent: Any, ticket: Ticket, tag: Any
) -> None:
    assert (
        _post(
            http, client_user, f"{SUPPORT}/{ticket.pk}/priority", {"priority": "urgent"}
        ).status_code
        == 403
    )
    assert (
        _data(_post(http, agent, f"{SUPPORT}/{ticket.pk}/priority", {"priority": "urgent"}))[
            "priority"
        ]
        == "urgent"
    )
    assert _data(_post(http, agent, f"{SUPPORT}/{ticket.pk}/tags", {"tags": ["escalated"]}))[
        "tags"
    ] == ["escalated"]


@pytest.mark.django_db
def test_an_unknown_tag_is_a_400(http: Client, agent: Any, ticket: Ticket) -> None:
    assert _post(http, agent, f"{SUPPORT}/{ticket.pk}/tags", {"tags": ["nope"]}).status_code == 400


@pytest.mark.django_db
def test_inviting_is_staff_only(
    http: Client, client_user: Any, agent: Any, second_agent: Any, ticket: Ticket
) -> None:
    refused = _post(
        http, client_user, f"{SUPPORT}/{ticket.pk}/participants", {"account": str(second_agent.pk)}
    )
    assert refused.status_code == 403

    allowed = _post(
        http,
        agent,
        f"{SUPPORT}/{ticket.pk}/participants",
        {"account": str(second_agent.pk), "role": "agent"},
    )
    assert allowed.status_code == 201
    assert _data(allowed)["user"]["username"] == "alan"


# -- rating -----------------------------------------------------------------


@pytest.mark.django_db
def test_rating_a_settled_ticket(
    http: Client, client_user: Any, agent: Any, ticket: Ticket
) -> None:
    assert (
        _post(http, client_user, f"{SUPPORT}/{ticket.pk}/rating", {"score": 5}).status_code == 403
    )

    _post(http, agent, f"{SUPPORT}/{ticket.pk}/close")

    response = _post(
        http, client_user, f"{SUPPORT}/{ticket.pk}/rating", {"score": 5, "comment": "Sorted"}
    )
    assert _data(response) == {"ticket": str(ticket.pk), "rating": 5, "comment": "Sorted"}


@pytest.mark.django_db
def test_an_agent_cannot_rate_their_own_handling(http: Client, agent: Any, ticket: Ticket) -> None:
    _post(http, agent, f"{SUPPORT}/{ticket.pk}/close")

    assert _post(http, agent, f"{SUPPORT}/{ticket.pk}/rating", {"score": 5}).status_code == 403


@pytest.mark.django_db
def test_a_rating_out_of_range_is_a_400(
    http: Client, client_user: Any, agent: Any, ticket: Ticket
) -> None:
    _post(http, agent, f"{SUPPORT}/{ticket.pk}/close")

    assert (
        _post(http, client_user, f"{SUPPORT}/{ticket.pk}/rating", {"score": 9}).status_code == 400
    )


# -- the desk's own reading -------------------------------------------------


@pytest.mark.django_db
def test_categories_are_readable_by_a_client(http: Client, client_user: Any, category: Any) -> None:
    rows = _data(http.get(f"{SUPPORT}/categories", **auth(client_user)))

    assert [row["slug"] for row in rows] == ["billing"]
    assert rows[0]["first_response_minutes"] == 60


@pytest.mark.django_db
def test_tags_canned_replies_and_stats_are_staff_only(
    http: Client, client_user: Any, agent: Any, tag: Any, canned: Any, ticket: Ticket
) -> None:
    for path in ("tags", "canned-replies", "stats"):
        assert http.get(f"{SUPPORT}/{path}", **auth(client_user)).status_code == 403
        assert http.get(f"{SUPPORT}/{path}", **auth(agent)).status_code == 200


@pytest.mark.django_db
def test_the_statistics_endpoint(http: Client, agent: Any, ticket: Ticket) -> None:
    stats = _data(http.get(f"{SUPPORT}/stats", **auth(agent)))

    assert stats["total"] == 1
    assert stats["open"] == 1
    assert stats["unassigned"] == 1
    assert stats["satisfaction"] is None


# -- uploads ----------------------------------------------------------------


@pytest.mark.django_db
def test_uploading_a_file_and_attaching_it(http: Client, client_user: Any, ticket: Ticket) -> None:
    upload = http.post(
        f"{SUPPORT}/uploads",
        {"file": SimpleUploadedFile("screenshot.png", b"pretend-png")},
        **auth(client_user),
    )
    assert upload.status_code == 201
    staged = _data(upload)
    assert staged["name"] == "screenshot.png"
    assert staged["size"] == len(b"pretend-png")

    message = _post(
        http,
        client_user,
        f"{SUPPORT}/{ticket.pk}/messages",
        {"body": "See this", "upload_ids": [staged["id"]]},
    )
    assert [item["name"] for item in _data(message)["attachments"]] == ["screenshot.png"]


@pytest.mark.django_db
def test_a_file_the_desk_does_not_accept_is_a_400(http: Client, client_user: Any) -> None:
    response = http.post(
        f"{SUPPORT}/uploads",
        {"file": SimpleUploadedFile("payload.exe", b"MZ")},
        **auth(client_user),
    )

    assert response.status_code == 400


@pytest.mark.django_db
@override_settings(SUPPORT_MAX_UPLOAD_BYTES=4)
def test_a_file_over_the_limit_is_a_400(http: Client, client_user: Any) -> None:
    response = http.post(
        f"{SUPPORT}/uploads",
        {"file": SimpleUploadedFile("big.png", b"much too long")},
        **auth(client_user),
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_somebody_elses_upload_id_is_a_400(
    http: Client, client_user: Any, other_client: Any, ticket: Ticket
) -> None:
    theirs = Upload.objects.create(owner=other_client, name="theirs.png", url="/theirs.png")

    response = _post(
        http,
        client_user,
        f"{SUPPORT}/{ticket.pk}/messages",
        {"body": "Mine now", "upload_ids": [str(theirs.pk)]},
    )

    assert response.status_code == 400


# -- typing -----------------------------------------------------------------


@pytest.mark.django_db
def test_typing_over_http_is_scoped_to_a_thread_you_can_see(
    http: Client, client_user: Any, other_client: Any, ticket: Ticket
) -> None:
    assert _post(http, client_user, f"{SUPPORT}/{ticket.pk}/typing").status_code == 200
    assert _post(http, other_client, f"{SUPPORT}/{ticket.pk}/typing").status_code == 404


# -- rooms over HTTP --------------------------------------------------------


def test_a_channel_is_opened_and_then_found_by_somebody_else(
    http: Client, client_user: Any, other_client: Any
) -> None:
    created = _post(http, client_user, f"{SUPPORT}/channels", {"name": "General"})
    assert created.status_code == 201

    found = _data(http.get(f"{SUPPORT}/channels", **auth(other_client)))

    assert [row["subject"] for row in found] == ["General"]
    assert found[0]["joined"] is False


def test_a_channel_is_joined_and_left_over_http(
    http: Client, client_user: Any, other_client: Any
) -> None:
    channel = _data(_post(http, client_user, f"{SUPPORT}/channels", {"name": "General"}))

    joined = _post(http, other_client, f"{SUPPORT}/{channel['id']}/join")
    left = _post(http, other_client, f"{SUPPORT}/{channel['id']}/leave")

    assert joined.status_code == 201
    assert _data(left)["left"] is True


def test_a_group_is_invisible_to_an_agent_over_http(
    http: Client, client_user: Any, other_client: Any, agent: Any
) -> None:
    """The same rule the service enforces, asserted through the door clients use."""
    group = _data(
        _post(
            http,
            client_user,
            f"{SUPPORT}/groups",
            {"name": "Ours", "members": [str(other_client.pk)]},
        )
    )

    assert http.get(f"{SUPPORT}/{group['id']}", **auth(agent)).status_code == 404


def test_a_private_chat_over_http_is_the_same_thread_both_times(
    http: Client, client_user: Any, other_client: Any
) -> None:
    first = _post(http, client_user, f"{SUPPORT}/direct", {"account": str(other_client.pk)})
    second = _post(http, other_client, f"{SUPPORT}/direct", {"account": str(client_user.pk)})

    assert first.status_code == 200
    assert _data(first)["id"] == _data(second)["id"]


def test_an_agent_cannot_claim_a_channel_over_http(
    http: Client, client_user: Any, agent: Any
) -> None:
    """A channel is not queue work, and must never land in somebody's queue."""
    channel = _data(_post(http, client_user, f"{SUPPORT}/channels", {"name": "General"}))

    assert _post(http, agent, f"{SUPPORT}/{channel['id']}/claim").status_code == 404
