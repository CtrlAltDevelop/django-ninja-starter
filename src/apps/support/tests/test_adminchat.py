"""The live desk screen: who may open it, and what it hands its script.

The screen itself is a socket client, so there is very little server behaviour
to test -- and that is the point of the tests here. What can still go wrong is
the *door*: a route that is not mounted, an account without the permission being
let in, a page rendered against a deployment that publishes no socket and saying
nothing about it, and the configuration block the script reads being wrong or
missing. Each of those is silent in a browser and loud here.
"""

from typing import Any

import pytest
from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.support.admin import TicketAdmin
from apps.support.adminchat import socket_available
from apps.support.models import Ticket

pytestmark = pytest.mark.django_db


def _admin() -> TicketAdmin:
    return TicketAdmin(Ticket, AdminSite())


def _request(user: Any) -> Any:
    request = RequestFactory().get("/admin/support/ticket/chat/")
    request.user = user
    return request


def test_the_desk_is_mounted_under_the_ticket_admin() -> None:
    assert reverse("admin:support_live_chat") == "/admin/support/ticket/chat/"


def test_the_route_sits_ahead_of_the_object_route() -> None:
    """Otherwise ``chat/`` is read as a primary key and answers a 404."""
    names = [pattern.name for pattern in _admin().get_urls()]
    assert names[0] == "support_live_chat"


def test_an_agent_who_may_see_tickets_gets_the_screen(agent: Any) -> None:
    from django.contrib.auth.models import Permission

    agent.user_permissions.add(Permission.objects.get(codename="view_ticket"))
    agent = type(agent).objects.get(pk=agent.pk)  # permissions are cached per instance
    response = _admin().live_chat_view(_request(agent))
    assert response.status_code == 200
    assert response.template_name == "admin/support/live_chat.html"


def test_a_member_of_staff_without_the_permission_is_refused(agent: Any) -> None:
    with pytest.raises(PermissionDenied):
        _admin().live_chat_view(_request(agent))


def test_the_page_hands_its_script_the_socket_path(admin_user: Any) -> None:
    context = _admin().live_chat_context(_request(admin_user))

    assert context["config"]["ws_path"] == settings.SUPPORT_WS_PATH
    assert context["config"]["user_id"] == str(admin_user.pk)


def test_the_vocabularies_are_rendered_rather_than_fetched(admin_user: Any, category: Any) -> None:
    """A screen that waits for a round trip to draw its own dropdown flickers."""
    context = _admin().live_chat_context(_request(admin_user))
    assert [slug for slug, _ in context["kinds"]] == ["chat", "ticket"]
    assert {row["slug"] for row in context["categories"]} == {category.slug}
    assert context["statuses"] and context["priorities"]


@override_settings(SUPPORT_TRANSPORTS=("rest",))
def test_a_deployment_without_the_socket_says_so_rather_than_hanging(admin_user: Any) -> None:
    assert socket_available() is False
    assert _admin().live_chat_context(_request(admin_user))["socket_available"] is False


def test_the_screen_opens_one_socket_and_it_is_this_app_s(admin_user: Any) -> None:
    """The admin's live bell carries the notification feed on every page.

    A second connection from here would announce every announcement twice, in
    two corners of one screen, so the page is handed no path to open one with.
    """
    config = _admin().live_chat_context(_request(admin_user))["config"]

    assert config["ws_path"] == settings.SUPPORT_WS_PATH
    assert "notifications_ws_path" not in config


def test_the_screen_renders_inside_the_admin(client: Any, admin_user: Any) -> None:
    """End to end through the theme, which is where a template error shows up."""
    client.force_login(admin_user)
    response = client.get(reverse("admin:support_live_chat"))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'id="desk-config"' in body
    # The live corner is admin chrome and should be on this page like any other.
    assert reverse("admin-live-script") in body


def test_the_desk_is_in_the_sidebar(client: Any, admin_user: Any) -> None:
    from infrastructure.common.adminui import sidebar_navigation

    request = _request(admin_user)
    titles = {item["title"] for group in sidebar_navigation(request) for item in group["items"]}
    assert "Live chat" in titles
