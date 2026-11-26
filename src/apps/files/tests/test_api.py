"""The HTTP transport."""

from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client

from apps.files.tests.conftest import PNG, bearer

LIST = "/api/v1/files"


def _upload(client: Client, user: Any, name: str = "shot.png", content: bytes = PNG) -> Any:
    return client.post(LIST, {"file": SimpleUploadedFile(name, content)}, **bearer(user))


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", LIST),
        ("post", LIST),
        ("get", f"{LIST}/00000000-0000-0000-0000-000000000000"),
        ("get", f"{LIST}/00000000-0000-0000-0000-000000000000/download"),
        ("delete", f"{LIST}/00000000-0000-0000-0000-000000000000"),
    ],
)
def test_every_endpoint_needs_a_credential(method: str, path: str, transactional_db: None) -> None:
    assert getattr(Client(), method)(path).status_code == 401


def test_upload_list_download_delete(alice: Any) -> None:
    client = Client()
    created = _upload(client, alice)
    assert created.status_code == 201, created.content
    file_id = created.json()["data"]["id"]

    assert client.get(LIST, **bearer(alice)).json()["data"]["total"] == 1
    download = client.get(f"{LIST}/{file_id}/download", **bearer(alice))
    assert download.status_code == 200
    assert download["Content-Type"] == "image/png"
    assert download["Content-Disposition"].startswith("attachment")
    assert b"".join(download.streaming_content) == PNG

    assert client.delete(f"{LIST}/{file_id}", **bearer(alice)).json()["data"]["deleted"]
    assert client.get(f"{LIST}/{file_id}", **bearer(alice)).status_code == 404


def test_a_disguised_file_is_a_400(alice: Any) -> None:
    assert _upload(Client(), alice, "shot.pdf").status_code == 400


def test_another_account_gets_404_and_staff_get_through(alice: Any, bob: Any, staff: Any) -> None:
    client = Client()
    file_id = _upload(client, alice).json()["data"]["id"]

    for path in (f"{LIST}/{file_id}", f"{LIST}/{file_id}/download"):
        assert client.get(path, **bearer(bob)).status_code == 404
        assert client.get(path, **bearer(staff)).status_code == 200
    assert client.delete(f"{LIST}/{file_id}", **bearer(bob)).status_code == 404
    assert client.get(LIST, **bearer(bob)).json()["data"]["total"] == 0
