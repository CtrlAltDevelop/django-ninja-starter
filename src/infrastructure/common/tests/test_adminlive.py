"""The admin's live corner: what it opens, and what it does when there is nothing.

The script itself runs in a browser and is not tested here. What is tested is
everything a deployment can get wrong without seeing an error: a script that
opens a socket this project does not publish, one that opens nothing at all and
is still served, and a path rendered into it that does not match the path the
socket is actually mounted on -- which would fail as a connection that never
opens, in production, silently.
"""

import pytest
from django.test import RequestFactory, override_settings
from django.urls import NoReverseMatch, reverse

from infrastructure.common.adminlive import (
    anything_live,
    live_feeds,
    live_script,
    live_script_url,
)


def _rendered() -> str:
    response = live_script(RequestFactory().get("/admin/live.js"))
    response.render()
    return response.content.decode()


def test_the_feeds_are_the_paths_the_sockets_are_mounted_on() -> None:
    """The one thing that must agree with `config.sockets`, or nothing connects."""
    from config.sockets import websocket_routes

    mounted = {path for path, _ in websocket_routes()}
    for path in live_feeds().values():
        if path:
            assert path in mounted


def test_the_script_is_served_as_javascript() -> None:
    response = live_script(RequestFactory().get("/admin/live.js"))
    assert response["Content-Type"].startswith("text/javascript")


def test_the_script_carries_this_deployments_paths() -> None:
    from django.conf import settings

    body = _rendered()
    assert settings.NOTIFICATIONS_WS_PATH in body
    assert settings.SUPPORT_WS_PATH in body


def test_the_bell_links_to_the_desk_when_there_is_one() -> None:
    assert reverse("admin:support_live_chat") in _rendered()


@override_settings(NOTIFICATIONS_TRANSPORTS=("rest",), SUPPORT_TRANSPORTS=("rest",))
def test_nothing_is_live_when_no_app_publishes_a_socket() -> None:
    """The project skips the URL and the script tag entirely in that case."""
    assert anything_live() is False
    assert live_feeds() == {"notifications": "", "support": ""}


@override_settings(SUPPORT_ENABLED=False)
def test_a_feed_that_is_switched_off_is_not_opened() -> None:
    assert live_feeds()["support"] == ""
    assert "support" not in _rendered().split("SUPPORT_WS = ")[1].split("\n")[0]


def test_unfold_is_given_a_url_it_can_render() -> None:
    try:
        assert live_script_url(RequestFactory().get("/admin/")) == reverse("admin-live-script")
    except NoReverseMatch:  # pragma: no cover - only where no app publishes a socket
        pytest.skip("this deployment publishes no socket, so the script is not mounted")


@pytest.mark.django_db
def test_the_script_is_actually_served_and_not_only_reversed(client: object) -> None:
    """It sits under `admin/`, whose include ends in a catch-all 404.

    Reversing proves the route exists; only a request proves it is reached. A
    route registered after `admin/` reverses perfectly and is never served.
    """
    response = client.get(reverse("admin-live-script"))  # type: ignore[attr-defined]

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/javascript")
