"""The GraphQL transport: bytes travel base64-encoded."""

import base64
import json
from typing import Any

from django.test import Client

from apps.files.tests.conftest import PNG, bearer

UPLOAD = (
    "mutation($n: String!, $c: String!) { uploadFile(name: $n, contentBase64: $c) { id name } }"
)
LIST = "{ files { total files { id } } }"
CONTENT = "query($id: String!) { fileContent(fileId: $id) { contentBase64 file { contentType } } }"
DELETE = "mutation($id: String!) { deleteFile(fileId: $id) }"


def _graph(user: Any, query: str, **variables: Any) -> dict[str, Any]:
    response = Client().post(
        "/graphql",
        data={"query": query, "variables": variables},
        content_type="application/json",
        **(bearer(user) if user else {}),
    )
    assert response.status_code == 200, response.content
    return json.loads(response.content)


def test_round_trip(alice: Any, bob: Any) -> None:
    encoded = base64.b64encode(PNG).decode()
    file_id = _graph(alice, UPLOAD, n="a.png", c=encoded)["data"]["uploadFile"]["id"]

    assert _graph(alice, LIST)["data"]["files"]["total"] == 1
    assert _graph(bob, LIST)["data"]["files"]["total"] == 0
    content = _graph(alice, CONTENT, id=file_id)["data"]["fileContent"]
    assert content["contentBase64"] == encoded
    assert content["file"]["contentType"] == "image/png"
    assert _graph(bob, CONTENT, id=file_id)["errors"]
    assert _graph(bob, DELETE, id=file_id)["errors"]
    assert _graph(alice, DELETE, id=file_id)["data"]["deleteFile"] is True


def test_refusals(alice: Any, settings: Any) -> None:
    assert _graph(None, LIST)["errors"]
    assert _graph(alice, UPLOAD, n="a.pdf", c=base64.b64encode(PNG).decode())["errors"]
    assert _graph(alice, UPLOAD, n="a.png", c="not base64!")["errors"]
    settings.FILES_MAX_UPLOAD_BYTES = 8
    assert _graph(alice, UPLOAD, n="a.png", c=base64.b64encode(PNG).decode())["errors"]
