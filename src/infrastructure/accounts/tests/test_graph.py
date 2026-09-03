"""The account, read and updated over GraphQL.

Same service, same refusals, same credential as the REST router at `/users` --
only the shape of the question and of the answer differ.
"""

import json
from typing import Any

import pytest
from django.test import Client

ME_QUERY = """
{ me { username email isStaff profile { displayName bio dateOfBirth } } }
"""
UPDATE_MUTATION = """
mutation($profile: ProfileInput!) {
  updateProfile(profile: $profile) {
    username
    profile { displayName bio dateOfBirth marketingOptIn }
  }
}
"""
PASSWORD = "corr3ct-horse-battery"


def graphql(query: str, token: str | None = None, **variables: Any) -> dict[str, Any]:
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    response = Client().post(
        "/graphql",
        data={"query": query, "variables": variables},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.content
    return json.loads(response.content)


@pytest.fixture
def token(db: None) -> str:
    response = Client().post(
        "/api/v1/auth/password/signup",
        {"identifier": "zoe", "password": PASSWORD, "email": "zoe@example.com"},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    access: str = response.json()["data"]["credentials"]["access_token"]
    return access


def test_me_describes_the_signed_in_account(token: str) -> None:
    body = graphql(ME_QUERY, token)

    assert body["data"]["me"]["username"] == "zoe"
    assert body["data"]["me"]["email"] == "zoe@example.com"
    assert body["data"]["me"]["isStaff"] is False


def test_me_refuses_a_caller_with_no_credential(db: None) -> None:
    """The refusal carries the same `title` a REST client would key on."""
    body = graphql(ME_QUERY)

    assert body["data"] is None
    assert body["errors"][0]["extensions"]["title"] == "AUTHENTICATION_REQUIRED"
    assert body["errors"][0]["extensions"]["status"] == 401


def test_update_profile_changes_only_the_fields_it_was_given(token: str) -> None:
    graphql(UPDATE_MUTATION, token, profile={"displayName": "Zoe", "bio": "Hello"})
    body = graphql(UPDATE_MUTATION, token, profile={"bio": "Updated"})

    profile = body["data"]["updateProfile"]["profile"]
    assert profile["displayName"] == "Zoe"
    assert profile["bio"] == "Updated"


def test_an_empty_update_is_refused_the_way_the_rest_route_refuses_it(token: str) -> None:
    body = graphql(UPDATE_MUTATION, token, profile={})

    assert body["errors"][0]["extensions"]["title"] == "VALIDATION_ERROR"


def test_a_malformed_date_is_refused(token: str) -> None:
    body = graphql(UPDATE_MUTATION, token, profile={"dateOfBirth": "not-a-date"})

    assert body["errors"][0]["extensions"]["title"] == "VALIDATION_ERROR"
    assert "ISO date" in body["errors"][0]["message"]
