from django.test import Client

from apps.notes.services import EXCERPT_LENGTH
from apps.notes.tests.test_v1 import _account, _write

V1 = "/api/v1/notes/"
V2 = "/api/v2/notes/"


def test_the_list_carries_excerpts_instead_of_bodies(db: None) -> None:
    client = Client()
    headers = _account(client)
    _write(client, headers, "Long one", body="x" * (EXCERPT_LENGTH + 50))

    listed = client.get(V2, **headers).json()["data"]

    assert "body" not in listed[0]
    assert listed[0]["excerpt"].endswith("…")
    assert len(listed[0]["excerpt"]) == EXCERPT_LENGTH + 1


def test_search_matches_the_title_or_the_body(db: None) -> None:
    client = Client()
    headers = _account(client)
    _write(client, headers, "Shopping", body="oat milk")
    _write(client, headers, "Oat pancakes", body="flour")
    _write(client, headers, "Unrelated", body="nothing")

    found = client.get(f"{V2}?q=oat", **headers).json()["data"]

    assert {note["title"] for note in found} == {"Shopping", "Oat pancakes"}


def test_the_window_pages_through_the_list(db: None) -> None:
    client = Client()
    headers = _account(client)
    for index in range(5):
        _write(client, headers, f"Note {index}")

    page = client.get(f"{V2}?limit=2&offset=2", **headers).json()["data"]

    assert len(page) == 2


def test_both_versions_serve_the_same_rows(db: None) -> None:
    client = Client()
    headers = _account(client)
    created = _write(client, headers, "Shared")

    assert client.get(f"{V1}{created['id']}", **headers).json()["data"]["title"] == "Shared"
    assert client.get(f"{V2}{created['id']}", **headers).json()["data"]["title"] == "Shared"
