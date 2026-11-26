"""The three transports, each against a real credential."""

from collections.abc import Callable
from typing import Any

import grpc
import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from google.protobuf.empty_pb2 import Empty

from apps.webhooks.grpc import webhooks_pb2, webhooks_pb2_grpc
from apps.webhooks.tests.conftest import access_token, resolving_to

BASE = "/api/v1/webhooks"
URL = "https://hooks.example.test/in"


def _bearer(user: Any) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token(user)}"}


def test_rest_round_trip(alice: Any, bob: Any) -> None:
    client = Client()
    created = client.post(
        BASE,
        {"url": URL, "events": ["order.paid"]},
        content_type="application/json",
        **_bearer(alice),
    )
    assert created.status_code == 201, created.content
    endpoint = created.json()["data"]
    assert len(endpoint["secret"]) == 64

    listed = client.get(BASE, **_bearer(alice)).json()["data"]
    assert [row["id"] for row in listed] == [endpoint["id"]]
    assert listed[0]["secret"] == ""
    assert client.get(BASE, **_bearer(bob)).json()["data"] == []

    rotated = client.post(f"{BASE}/{endpoint['id']}/rotate-secret", **_bearer(alice)).json()
    assert rotated["data"]["secret"] != endpoint["secret"]
    assert client.get(f"{BASE}/{endpoint['id']}/deliveries", **_bearer(alice)).status_code == 200
    assert client.delete(f"{BASE}/{endpoint['id']}", **_bearer(bob)).status_code == 404
    assert client.delete(f"{BASE}/{endpoint['id']}", **_bearer(alice)).status_code == 204


def test_rest_refuses_a_private_target(alice: Any) -> None:
    with resolving_to("169.254.169.254"):
        response = Client().post(
            BASE, {"url": URL, "events": ["x"]}, content_type="application/json", **_bearer(alice)
        )
    assert response.status_code == 400


def test_rest_needs_a_credential(db: None) -> None:
    assert Client().get(BASE).status_code == 401


def test_graphql_round_trip(alice: Any) -> None:
    client = Client()
    create = 'mutation { createWebhookEndpoint(url: "' + URL + '", events: ["a.b"]) { id secret } }'
    made = client.post(
        "/graphql", {"query": create}, content_type="application/json", **_bearer(alice)
    ).json()
    endpoint_id = made["data"]["createWebhookEndpoint"]["id"]

    listed = client.post(
        "/graphql",
        {"query": "{ webhookEndpoints { id events } }"},
        content_type="application/json",
        **_bearer(alice),
    ).json()
    assert listed["data"]["webhookEndpoints"] == [{"id": endpoint_id, "events": ["a.b"]}]


@pytest.fixture
def carol(transactional_db: None) -> Any:
    return get_user_model().objects.create_user(username="carol", email="carol@example.test")


def test_grpc_round_trip(carol: Any, grpc_call: Callable[..., Any]) -> None:
    Stub = webhooks_pb2_grpc.WebhookControllerStub
    token = access_token(carol)
    made = grpc_call(Stub, "Create", webhooks_pb2.CreateRequest(url=URL, events=["a.b"]), token)
    listed = grpc_call(Stub, "List", Empty(), token)

    assert [row.id for row in listed.endpoints] == [made.id]
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(Stub, "Delete", webhooks_pb2.DeleteRequest(endpoint_id="nope"), token)
    assert refusal.value.code() is grpc.StatusCode.NOT_FOUND
