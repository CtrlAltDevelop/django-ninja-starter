from django.test import Client

PASSWORD = "corr3ct-horse-battery"
NOTES = "/api/v1/notes/"


def _account(client: Client, identifier: str = "ada") -> dict[str, str]:
    """Sign up through the password app and return the header its token goes in."""
    response = client.post(
        "/api/v1/auth/password/signup",
        {
            "identifier": identifier,
            "password": PASSWORD,
            "email": f"{identifier}@example.com",
        },
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return {"HTTP_AUTHORIZATION": f"Bearer {response.json()['credentials']['access_token']}"}


def _write(client: Client, headers: dict[str, str], title: str, **fields: object) -> dict:
    response = client.post(
        NOTES,
        {"title": title, **fields},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 201, response.content
    return response.json()


def test_a_note_round_trips(db: None) -> None:
    client = Client()
    headers = _account(client)

    created = _write(client, headers, "Groceries", body="Oat milk")
    read = client.get(f"{NOTES}{created['id']}", **headers)

    assert read.status_code == 200
    assert read.json()["title"] == "Groceries"
    assert read.json()["body"] == "Oat milk"


def test_anonymous_callers_are_refused(db: None) -> None:
    assert Client().get(NOTES).status_code == 401


def test_notes_are_scoped_to_their_owner(db: None) -> None:
    client = Client()
    mine = _account(client, "ada")
    created = _write(client, mine, "Mine")
    theirs = _account(Client(), "grace")

    listed = client.get(NOTES, **theirs)
    fetched = client.get(f"{NOTES}{created['id']}", **theirs)

    assert listed.json() == []
    assert fetched.status_code == 404, "another account's id must not be distinguishable"


def test_patch_changes_only_what_was_sent(db: None) -> None:
    client = Client()
    headers = _account(client)
    created = _write(client, headers, "Draft", body="Keep me")

    response = client.patch(
        f"{NOTES}{created['id']}",
        {"title": "Final"},
        content_type="application/json",
        **headers,
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Final"
    assert response.json()["body"] == "Keep me"


def test_deleting_a_note_makes_it_gone(db: None) -> None:
    client = Client()
    headers = _account(client)
    created = _write(client, headers, "Temporary")

    deleted = client.delete(f"{NOTES}{created['id']}", **headers)

    assert deleted.status_code == 200
    assert client.get(f"{NOTES}{created['id']}", **headers).status_code == 404


def test_pinned_notes_sort_ahead_of_newer_ones(db: None) -> None:
    client = Client()
    headers = _account(client)
    _write(client, headers, "Pinned", pinned=True)
    _write(client, headers, "Newer")

    titles = [note["title"] for note in client.get(NOTES, **headers).json()]

    assert titles == ["Pinned", "Newer"]
