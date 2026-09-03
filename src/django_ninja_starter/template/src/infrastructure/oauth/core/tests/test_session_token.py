"""Trading an admin session for a bearer token, which is what makes Swagger usable.

The API reads the ``Authorization`` header and ignores cookies, so a staff member
signed into the admin has, as far as ``/api/docs`` is concerned, no credential at
all. These cover the endpoint that closes that gap -- and, just as importantly,
the three cases where it must refuse to.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from infrastructure.auth.core.models import AuthEvent
from infrastructure.oauth.core.services import ADMIN_SESSION_METHOD

pytestmark = pytest.mark.django_db

ENDPOINT = "/api/v1/auth/token/from-session"
ROOT = Path(__file__).resolve().parents[5]


@pytest.fixture
def staff() -> Any:
    return get_user_model().objects.create_user(
        username="ada", email="ada@example.com", is_staff=True
    )


@pytest.fixture
def ordinary() -> Any:
    return get_user_model().objects.create_user(username="wren", email="wren@example.com")


def _signed_in(user: Any) -> Client:
    """A client holding the cookie the admin login would have left behind."""
    client = Client()
    client.force_login(user)
    return client


def test_an_admin_session_mints_a_credential_the_api_honours(staff: Any) -> None:
    client = _signed_in(staff)
    assert client.get("/api/v1/users/me").status_code == 401, "the cookie alone is not a credential"

    minted = client.post(ENDPOINT)

    assert minted.status_code == 200, minted.content
    credentials = minted.json()["data"]
    assert credentials["token_type"] == "bearer"
    # A separate client, carrying nothing but the token: the proof that what came
    # back stands on its own rather than riding the cookie.
    me = Client().get(
        "/api/v1/users/me", HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}"
    )
    assert me.status_code == 200, me.content
    assert me.json()["data"]["username"] == "ada"


def test_the_session_outlives_the_minting(staff: Any) -> None:
    """The point of not being an exchange: opening the docs must not sign you out.

    The social ``/exchange`` beside this one deliberately consumes the session.
    Doing that here would log a developer out of the admin every time the
    documentation page loaded, and the second load would fail.
    """
    client = _signed_in(staff)

    assert client.post(ENDPOINT).status_code == 200
    assert "_auth_user_id" in client.session, "the admin session was consumed"
    assert client.post(ENDPOINT).status_code == 200, "a second docs page load must work too"


def test_minting_is_recorded_in_the_audit_trail(staff: Any) -> None:
    """It issues a real credential, so it is a real login and is filed as one."""
    _signed_in(staff).post(ENDPOINT)

    event = AuthEvent.objects.get()
    assert event.user_id == staff.pk
    assert event.method == ADMIN_SESSION_METHOD


def test_an_ordinary_session_is_refused(ordinary: Any) -> None:
    """Signed in is not the bar; signed into the *admin* is."""
    response = _signed_in(ordinary).post(ENDPOINT)

    assert response.status_code == 403
    assert response.json()["title"] == "FORBIDDEN"


def test_no_session_is_refused() -> None:
    response = Client().post(ENDPOINT)

    assert response.status_code == 401
    assert response.json()["title"] == "NOT_SIGNED_IN"


def test_a_deactivated_staff_account_is_refused(staff: Any) -> None:
    client = _signed_in(staff)
    staff.is_active = False
    staff.save(update_fields=["is_active"])

    assert client.post(ENDPOINT).status_code == 401


# -- the setting -------------------------------------------------------------

# Registration happens at import, so a test process cannot un-publish a route it
# has already published. Asking a fresh interpreter is the only honest way to
# check that turning the setting off removes the route from the document.
_SCHEMA_PROBE = """
import sys
sys.path.insert(0, "src")
import django
django.setup()
from config.api import apis
paths = [path for path in apis["v1"].get_openapi_schema()["paths"] if "from-session" in path]
print("PATHS=" + ",".join(paths))
"""


def _published_paths(*, enabled: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", _SCHEMA_PROBE],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        env={
            **{key: value for key, value in os.environ.items() if not key.startswith("DJANGO_")},
            "DJANGO_SETTINGS_MODULE": "config.settings.development",
            "DJANGO_SECRET_KEY": "session-token-secret-key-long-enough-for-hs256",
            "DJANGO_AUTH_METHODS": "password",
            "DJANGO_AUTH_TOKEN_MODE": "rotation",
            "DJANGO_AUTH_SESSION_TOKEN_FOR_STAFF": enabled,
        },
    )
    line = next(line for line in result.stdout.splitlines() if line.startswith("PATHS="))
    return line.removeprefix("PATHS=")


def test_the_setting_decides_whether_the_route_exists_at_all() -> None:
    """Off means absent, not a 403: nothing should document a bridge it will refuse."""
    assert _published_paths(enabled="true") == "/api/v1/auth/token/from-session"
    assert _published_paths(enabled="false") == ""
