"""Boot one configuration of this project and prove it actually works.

Run as a subprocess by ``test_app_isolation.py``, because settings are read once
at import and a test process cannot un-enable an app it has already installed.
Enabling a single app therefore means a fresh interpreter, which is what this is.

    python tests/isolation_driver.py password

Exits zero and prints ``ok`` when the named scenario signs somebody in and the
credential it got back is honoured. Anything else is a failure, printed.
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _boot() -> None:
    import django

    django.setup()
    from django.core.management import call_command

    call_command("migrate", run_syncdb=True, verbosity=0)


def _client():  # type: ignore[no-untyped-def]
    """A client addressing a host the development settings actually allow.

    Django's test client calls itself ``testserver``, which these settings do not
    list -- and rightly so, since ALLOWED_HOSTS is one of the things worth being
    strict about.
    """
    from django.test import Client

    return Client(SERVER_NAME="localhost")


def _post(client, url: str, body: dict):  # type: ignore[no-untyped-def]
    return client.post(url, body, content_type="application/json")


def _why(response) -> str:  # type: ignore[no-untyped-def]
    """A one-line reason, rather than a screenful of Django's debug page."""
    try:
        return f"{response.status_code} {response.json()}"
    except ValueError:
        return f"{response.status_code} {response.content[:200]!r}"


def _outbox_code() -> str:
    from infrastructure.auth.core import delivery

    return delivery.outbox[-1].body.rsplit(": ", 1)[1]


def _assert_credential_is_honoured(client, credentials: dict) -> None:  # type: ignore[no-untyped-def]
    """The token must identify its owner through the project's own auth callable."""
    from django.test import RequestFactory

    from infrastructure.auth.core.sessions import resolve_request_user

    request = RequestFactory().get("/", HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}")
    user = resolve_request_user(request)
    assert user is not None, "the credential this login issued does not authenticate"


def password():  # type: ignore[no-untyped-def]
    client = _client()
    response = _post(
        client,
        "/api/v1/auth/password/signup",
        {"identifier": "zoe", "password": "corr3ct-horse-battery", "email": "zoe@example.com"},
    )
    assert response.status_code == 200, _why(response)
    return client, response.json()["data"]


def email_code():  # type: ignore[no-untyped-def]
    client = _client()
    start = _post(client, "/api/v1/auth/email-code/signup/start", {"email": "zoe@example.com"})
    assert start.status_code == 200, _why(start)
    response = _post(
        client,
        "/api/v1/auth/email-code/signup/verify",
        {"ticket": start.json()["data"]["ticket"], "code": _outbox_code()},
    )
    assert response.status_code == 200, _why(response)
    return client, response.json()["data"]


def sms_code():  # type: ignore[no-untyped-def]
    client = _client()
    start = _post(client, "/api/v1/auth/sms-code/signup/start", {"phone": "+14155550101"})
    assert start.status_code == 200, _why(start)
    response = _post(
        client,
        "/api/v1/auth/sms-code/signup/verify",
        {"ticket": start.json()["data"]["ticket"], "code": _outbox_code()},
    )
    assert response.status_code == 200, _why(response)
    return client, response.json()["data"]


def magic_link():  # type: ignore[no-untyped-def]
    from infrastructure.auth.core import delivery

    client = _client()
    start = _post(client, "/api/v1/auth/magic-link/signup/start", {"email": "zoe@example.com"})
    assert start.status_code == 200, _why(start)
    token = delivery.outbox[-1].body.split("token=", 1)[1].split("\n", 1)[0].strip()
    response = _post(client, "/api/v1/auth/magic-link/verify", {"token": token})
    assert response.status_code == 200, _why(response)
    return client, response.json()["data"]


SCENARIOS = {
    "password": password,
    "email_code": email_code,
    "sms_code": sms_code,
    "magic_link": magic_link,
}


def main() -> int:
    scenario = sys.argv[1]
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    _boot()

    client, body = SCENARIOS[scenario]()
    assert body["requires_second_factor"] is False, body
    credentials = body["credentials"]
    assert credentials["access_token"], body
    _assert_credential_is_honoured(client, credentials)

    # Whatever else is mounted in this configuration must accept it too.
    from django.conf import settings

    if settings.AUTH_TOKEN_MODE != "none":
        listed = client.get(
            "/api/v1/auth/token/sessions",
            HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}",
        )
        assert listed.status_code == 200, _why(listed)
        assert listed.json()["data"]["mode"] == settings.AUTH_TOKEN_MODE

    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
