"""How often one caller may ask, whichever endpoint they are asking.

Authentication says who may call something; these say how often. Each test
asserts the limit bites *and* that the neighbouring caller is untouched by it --
a throttle that counted everybody together would pass a test that only ever
checked the first half.
"""

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, RequestFactory

from infrastructure.common.throttling import rate_for

CATALOGUE = "/api/v1/shop/products"
LOGIN = "/api/v1/auth/password/login"


@pytest.fixture(autouse=True)
def _tight_limits(settings: Any) -> None:
    """Small numbers, so a test spends three requests rather than six hundred."""
    settings.API_THROTTLE_ANON = "3/min"
    settings.API_THROTTLE_AUTH = "5/min"
    settings.API_THROTTLE_LOGIN = "2/min"
    cache.clear()


def _token(user: Any) -> str:
    from infrastructure.auth.core.sessions import issue_credentials

    return issue_credentials(RequestFactory().post("/"), user, method="password").access_token


def test_an_anonymous_caller_is_cut_off_at_the_anon_rate(client: Client, db: None) -> None:
    """The public catalogue is the endpoint most worth scraping."""
    codes = [client.get(CATALOGUE).status_code for _ in range(5)]

    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429


def test_the_refusal_says_how_long_to_wait(client: Client, db: None) -> None:
    """A 429 without `Retry-After` is a client that retries immediately."""
    for _ in range(4):
        last = client.get(CATALOGUE)

    assert last.status_code == 429
    assert int(last["Retry-After"]) > 0


def test_a_credential_is_counted_on_its_own_budget(client: Client, db: None) -> None:
    """Not by IP: an office behind one NAT would otherwise throttle itself.

    The signed-in caller goes past the anonymous ceiling of three because the
    anonymous throttle does not apply to a request that proved an account.
    """
    user = get_user_model().objects.create_user(username="ada", email="ada@example.test")
    headers = {"HTTP_AUTHORIZATION": f"Bearer {_token(user)}"}

    codes = [client.get("/api/v1/shop/cart", **headers).status_code for _ in range(5)]

    assert codes == [200, 200, 200, 200, 200]


def test_two_accounts_do_not_share_one_budget(client: Client, db: None) -> None:
    users = [
        get_user_model().objects.create_user(username=name, email=f"{name}@example.test")
        for name in ("ada", "bob")
    ]
    first, second = ({"HTTP_AUTHORIZATION": f"Bearer {_token(user)}"} for user in users)

    for _ in range(5):
        client.get("/api/v1/shop/cart", **first)

    assert client.get("/api/v1/shop/cart", **first).status_code == 429
    assert client.get("/api/v1/shop/cart", **second).status_code == 200


def test_signing_in_is_held_to_the_tighter_login_rate(client: Client, db: None) -> None:
    """Two, not the catalogue's three.

    The per-account guess limits in the authentication core cannot see a
    credential-stuffing run, which makes one guess against each of fifty
    thousand accounts. This is the axis that can.
    """
    attempt = {"identifier": "nobody", "password": "wrong-password"}
    codes = []
    for _ in range(4):
        reply = client.post(LOGIN, attempt, content_type="application/json")
        codes.append(reply.status_code)

    assert 429 in codes
    assert codes.index(429) == 2


def test_a_deployment_can_turn_a_scope_off(client: Client, db: None, settings: Any) -> None:
    """An empty rate is how a project that limits at its CDN avoids counting twice.

    Read per request, so this takes effect on a running process rather than at
    the next restart -- which is also what makes `override_settings` work for
    whoever writes the next test.
    """
    settings.API_THROTTLE_ANON = ""

    assert [client.get(CATALOGUE).status_code for _ in range(6)] == [200] * 6


def test_turning_the_ordinary_limits_off_leaves_the_login_one_on(settings: Any) -> None:
    """The pair most worth being able to move independently."""
    settings.API_THROTTLE_ANON = ""
    settings.API_THROTTLE_AUTH = ""

    assert rate_for("anon") == ""
    assert rate_for("login") == "2/min"
