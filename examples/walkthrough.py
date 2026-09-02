#!/usr/bin/env python3
"""Exercise every app this starter ships, in a single run.

    python examples/walkthrough.py

Four login methods, four second factors, four social providers, a token mode,
the accounts and health endpoints, both feature apps the starter ships -- the
CMS and notifications, socket included -- the notes app you would write
yourself, the audit trail and the generated OpenAPI documents. All of it printed
as a transcript of the calls a real client would make.

What it runs against is the point. Not this checkout's ``src/``, but the example
project ``examples/build.py`` produces with the packaged generator: a tree with
its own ``.env``, its own registry, and the notes feature app registered at two
API versions. If the published package stops emitting something this tour needs,
the tour is what fails. It builds that project on first run and reuses it after.

Two further choices keep it offline:

*   It drives Django's test client in-process instead of a running server. The
    codes these flows send out are the whole point of the demo, and only a
    process holding the outbox can read them back. Every call still goes through
    URL routing, middleware, authentication and the view -- the same stack
    ``runserver`` uses, and the paths printed below are the real paths. The
    README has the curl equivalents for a live server.
*   The WebSocket is driven the same way: the ASGI application is called
    directly over the two queues a server would hand it, so the tour needs no
    uvicorn and no open port. ``runserver`` could not have served it anyway --
    Django's development server is WSGI, which is why the project ships ``make
    serve``.
*   It runs against a throwaway in-memory database, so the tour leaves the
    example project's own ``db.sqlite3`` exactly as ``build.py`` left it.

Anything already set in the shell wins, so one switch re-runs the whole tour
under a different token mode:

    DJANGO_AUTH_TOKEN_MODE=sliding python examples/walkthrough.py
"""

import argparse
import asyncio
import io
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import build

EXAMPLES = Path(__file__).resolve().parent

PASSWORD = "corr3ct-horse-battery-staple"
NEW_PASSWORD = "an0ther-horse-battery-staple"

# Set on the child process to say which project it is touring, and to tell it
# apart from the parent run that built that project and launched it.
IN_PROJECT = "WALKTHROUGH_PROJECT"

# Variables the child inherits from your shell, so that overriding one of them
# re-runs the tour under a different configuration.
PASSED_THROUGH = ("DJANGO_", "GOOGLE_", "APPLE_", "MICROSOFT_", "GITHUB_")

# The example project's .env already turns every app on and supplies the
# credentials each one demands. These are only what an offline, in-process tour
# needs on top of it: a database that never hits disk, delivery backends that
# hand the codes back to this process instead of to a carrier, and no waiting
# between sends. Anything already in the environment wins over all of them.
#
# The database is a *shared* in-memory one rather than a plain ``:memory:``,
# which SQLite gives privately to each connection. The notification socket
# reaches the ORM through ``sync_to_async``, so its queries run on a worker
# thread, and a thread gets a connection of its own: against a private
# ``:memory:`` it would open onto an empty database and every socket frame would
# fail on a missing table. ``mode=memory`` also makes Django treat a close as a
# no-op, so the database outlives the connection each request finishes with.
DEFAULTS = {
    "DJANGO_SETTINGS_MODULE": "config.settings.development",
    "DJANGO_DB_NAME": "file:walkthrough?mode=memory&cache=shared",
    "DJANGO_AUTH_SMS_BACKEND": "infrastructure.auth.core.delivery.LocMemSmsBackend",
    "DJANGO_AUTH_EMAIL_BACKEND": "infrastructure.auth.core.delivery.LocMemEmailBackend",
    "DJANGO_AUTH_RESEND_COOLDOWN_SECONDS": "0",
}

_COLOUR = sys.stdout.isatty()
BOLD = "\033[1m" if _COLOUR else ""
DIM = "\033[2m" if _COLOUR else ""
CYAN = "\033[36m" if _COLOUR else ""
GREEN = "\033[32m" if _COLOUR else ""
RED = "\033[31m" if _COLOUR else ""
OFF = "\033[0m" if _COLOUR else ""


class WalkthroughError(RuntimeError):
    """A call did not do what the tour says it does."""


def heading(number: int, title: str, apps: str, blurb: str) -> None:
    print(f"\n{BOLD}{'━' * 78}{OFF}")
    print(f"{BOLD}{number}. {title}{OFF}  {DIM}{apps}{OFF}")
    print(f"{DIM}{blurb}{OFF}")
    print(f"{BOLD}{'━' * 78}{OFF}")


def note(text: str) -> None:
    print(f"\n{DIM}# {text}{OFF}")


def shorten(value: Any, limit: int = 46) -> Any:
    """Keep tokens recognisable without letting one fill the terminal."""
    if isinstance(value, str) and len(value) > limit:
        return f"{value[: limit - 14]}…[{len(value)} chars]"
    if isinstance(value, dict):
        return {key: shorten(item, limit) for key, item in value.items()}
    if isinstance(value, list):
        return [shorten(item, limit) for item in value]
    return value


class Api:
    """The tour's client: one call, one printed line, one parsed body."""

    def __init__(self) -> None:
        from django.test import Client

        self.client = Client()
        self.token = ""

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        token: str | None = None,
        expect: int = 200,
        show: bool = True,
        headers: dict[str, str] | None = None,
    ) -> Any:
        bearer = self.token if token is None else token
        extra: dict[str, Any] = {"HTTP_AUTHORIZATION": f"Bearer {bearer}"} if bearer else {}
        if headers:
            extra["headers"] = headers
        send = getattr(self.client, method.lower())
        if payload is None:
            response = send(path, **extra)
        else:
            response = send(path, payload, content_type="application/json", **extra)

        body: Any = {}
        if response.headers.get("Content-Type", "").startswith("application/json"):
            body = response.json()

        ok = response.status_code == expect
        tint = GREEN if ok else RED
        auth = f" {DIM}+bearer{OFF}" if bearer else ""
        print(f"  {CYAN}{method:<6}{OFF} {path}{auth} {tint}→ {response.status_code}{OFF}")
        if show and body:
            rendered = json.dumps(shorten(body), indent=2, ensure_ascii=False)
            print("".join(f"  {DIM}│{OFF} {line}\n" for line in rendered.splitlines()), end="")
        if "Location" in response.headers:
            print(f"  {DIM}│ Location: {shorten(response.headers['Location'], 100)}{OFF}")
        if not ok:
            raise WalkthroughError(f"{method} {path} returned {response.status_code}, not {expect}")
        # Every JSON body arrives in the shared envelope. The whole envelope is
        # printed above, because that shape is the thing worth seeing; what the
        # rest of the tour works with is the payload inside it.
        if isinstance(body, dict) and "isSuccess" in body:
            return body["data"]
        return body

    def get(self, path: str, **kwargs: Any) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.request("POST", path, payload if payload is not None else {}, **kwargs)

    def patch(self, path: str, payload: dict[str, Any], **kwargs: Any) -> Any:
        return self.request("PATCH", path, payload, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    def sign_in_as(self, credentials: dict[str, Any]) -> None:
        self.token = credentials["access_token"]


class Socket:
    """The tour's WebSocket client: one frame, one printed line.

    There is no server and no port. An ASGI application is a callable taking a
    scope and two channels, so this provides the two queues uvicorn would have
    provided and calls it directly. What it calls is ``config.sockets``, the
    project's own websocket routing, rather than the app's consumer -- so the
    path below is resolved the way a real connection's would be, and a project
    that mounted nothing there would fail here too.
    """

    def __init__(self, path: str, *, query: str = "", label: str = "") -> None:
        from config.sockets import websocket_application

        self._application = websocket_application
        self._path = path
        self._label = label
        self._scope: dict[str, Any] = {
            "type": "websocket",
            "path": path,
            "query_string": query.encode(),
            "headers": [],
        }
        self._to_server: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._to_client: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "Socket":
        return self

    async def __aexit__(self, *exception: object) -> None:
        """Always take the connection down, including when the tour failed mid-flow.

        A pending consumer task outliving the loop it was started in is noise on
        top of whatever the real failure was.
        """
        if self._task is None:
            return
        self._to_server.put_nowait({"type": "websocket.disconnect", "code": 1000})
        task, self._task = self._task, None
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except Exception:
            task.cancel()

    async def _send(self, message: dict[str, Any]) -> None:
        await self._to_client.put(message)

    async def _next_message(self, timeout: float = 10.0) -> dict[str, Any]:
        """The next thing the consumer sent -- or why there will not be one.

        The consumer task is waited on alongside the queue so that a crash in it
        surfaces as its own exception straight away, rather than as a timeout ten
        seconds later that says nothing about the cause.
        """
        assert self._task is not None
        waiting = asyncio.ensure_future(self._to_client.get())
        done, _ = await asyncio.wait(
            {waiting, self._task}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if waiting in done:
            return waiting.result()
        waiting.cancel()
        if self._task in done:
            self._task.result()  # re-raises whatever the consumer failed with
            raise WalkthroughError(f"the socket at {self._path} ended without answering")
        raise WalkthroughError(f"the socket at {self._path} sent nothing back")

    async def open(self) -> dict[str, Any]:
        """Shake hands, then read the one frame the server always opens with."""
        self._task = asyncio.ensure_future(
            self._application(self._scope, self._to_server.get, self._send)
        )
        await self._to_server.put({"type": "websocket.connect"})
        handshake = await self._next_message()
        if handshake["type"] != "websocket.accept":
            raise WalkthroughError(f"the socket at {self._path} refused the handshake: {handshake}")
        offered = f" {DIM}+{self._label}{OFF}" if self._label else ""
        print(f"  {CYAN}{'WS':<6}{OFF} {self._path}{offered} {GREEN}→ accepted{OFF}")
        return await self.frame("ready")

    async def frame(self, expect: str, *, show: bool = True) -> dict[str, Any]:
        """Read one server frame, insisting it is the one the tour says it is."""
        message = await self._next_message()
        if message["type"] != "websocket.send":
            raise WalkthroughError(f"the socket closed instead of answering: {message}")
        frame: dict[str, Any] = json.loads(message["text"])
        ok = frame.get("type") == expect
        tint = GREEN if ok else RED
        print(f"  {DIM}│{OFF} {tint}← {frame.get('type')}{OFF}")
        if show:
            rendered = json.dumps(shorten(frame), indent=2, ensure_ascii=False)
            print("".join(f"  {DIM}│{OFF} {line}\n" for line in rendered.splitlines()), end="")
        if not ok:
            raise WalkthroughError(f"the socket sent {frame.get('type')!r}, not {expect!r}")
        return frame

    async def command(
        self, command: dict[str, Any], expect: str, *, show: bool = True
    ) -> dict[str, Any]:
        """Send one command and read the frame it is answered with."""
        print(f"  {DIM}│{OFF} {CYAN}→ {command['command']}{OFF}")
        await self._to_server.put({"type": "websocket.receive", "text": json.dumps(command)})
        return await self.frame(expect, show=show)


def outbox() -> list[Any]:
    from infrastructure.auth.core import delivery

    return delivery.outbox


def last_message(channel: str = "") -> Any:
    messages = [item for item in outbox() if not channel or item.channel == channel]
    if not messages:
        raise WalkthroughError(f"nothing was delivered over {channel or 'any channel'}")
    return messages[-1]


def delivered_code(channel: str = "") -> str:
    """Read back the code the flow just sent, as the recipient would."""
    from django.conf import settings

    message = last_message(channel)
    found = re.search(rf"\b(\d{{{settings.AUTH_CODE_DIGITS}}})\b", message.body)
    if not found:
        raise WalkthroughError(f"no code in the delivered message: {message.body!r}")
    print(f"  {DIM}│ delivered to {message.destination}: {message.body}{OFF}")
    return found.group(1)


def delivered_link_token() -> str:
    message = last_message("email")
    found = re.search(r"token=([A-Za-z0-9_.\-]+)", message.body)
    if not found:
        raise WalkthroughError(f"no link in the delivered message: {message.body!r}")
    print(f"  {DIM}│ delivered to {message.destination}: {shorten(message.body, 160)}{OFF}")
    return found.group(1)


def totp_code(secret: str, steps_ahead: int = 0) -> str:
    """A code for a chosen time step, so the tour can move past a spent one."""
    import pyotp

    from infrastructure.auth.twofactor.services import TOTP_PERIOD

    counter = int(time.time()) // TOTP_PERIOD + steps_ahead
    return pyotp.TOTP(secret, interval=TOTP_PERIOD).at(counter * TOTP_PERIOD)


def child_environment(project: Path) -> dict[str, str]:
    """Build the environment the tour runs under, shell overrides included."""
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        # The tour imports the *project's* config and infrastructure packages.
        "PYTHONPATH": str(project / "src"),
        IN_PROJECT: str(project),
        **{key: value for key, value in os.environ.items() if key.startswith(PASSED_THROUGH)},
    }
    for key, value in DEFAULTS.items():
        environment.setdefault(key, value)
    return environment


def relaunch_inside(project: Path) -> int:
    """Run this file again from inside the example project, and return its status.

    A child process rather than an import, because this checkout has a ``config``
    and an ``infrastructure`` package of its own one directory away, and the tour
    would silently be touring the wrong one. Starting a process whose path holds
    only the project settles which code answered -- the same isolation the
    project would have if it were the only thing on disk.
    """
    print(f"{DIM}Touring the example project in {project}{OFF}", flush=True)
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        cwd=project,
        check=False,
        env=child_environment(project),
    ).returncode


def configure() -> None:
    """Start Django against the example project and a throwaway database."""
    import django

    django.setup()

    from django.conf import settings
    from django.core.management import call_command

    # The in-process client presents itself as "testserver". A real server is
    # reached at one of the hosts development settings already allow.
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "testserver"]
    call_command("migrate", verbosity=0)


def section_configuration() -> None:
    from django.conf import settings

    heading(
        1,
        "What is installed",
        "config.settings",
        "Enabling an app is a name in an environment variable. Nothing else is "
        "installed, and nothing else has routes.",
    )
    print(f"  login methods    {', '.join(settings.AUTH_METHODS)}")
    print(f"  second factors   {', '.join(settings.AUTH_SECOND_FACTORS)}")
    print(f"  social providers {', '.join(settings.OAUTH_PROVIDERS)}")
    print(f"  token mode       {settings.AUTH_TOKEN_MODE}")
    print(f"  challenge store  {settings.AUTH_CHALLENGE_STORE.rsplit('.', 1)[-1]}")
    print(f"  content app      {'on' if settings.CMS_ENABLED else 'off'}")
    notifications = "off"
    if settings.NOTIFICATIONS_ENABLED:
        notifications = f"on, socket at {settings.NOTIFICATIONS_WS_PATH}"
    print(f"  notifications    {notifications}")
    print()
    for app in settings.INSTALLED_APPS:
        marker = " " if app.startswith("django.contrib") else "•"
        print(f"  {marker} {app}")


def section_checks() -> None:
    from django.core.management import call_command

    heading(
        2,
        "What those apps demand",
        "infrastructure.common.checks",
        "Every optional app declares the settings it cannot work without, so "
        "`manage.py check` reports on the apps you turned on and nothing else.",
    )
    stream = io.StringIO()
    call_command("check", stdout=stream, stderr=stream)
    output = stream.getvalue().strip() or "no messages"
    print("".join(f"  {line}\n" for line in output.splitlines()), end="")
    note(
        "Both warnings are deliberate, and both say the same thing about "
        "themselves: the challenge store and the notification broker are the "
        "in-process ones, so the tour needs no Redis -- and neither would reach "
        "a second worker, which is what they are warning about."
    )


def section_health(api: Api) -> None:
    heading(
        3,
        "Health",
        "infrastructure.common",
        "Two endpoints a load balancer and an orchestrator ask different "
        "questions with. Public, so a probe needs no credential.",
    )
    api.get("/api/v1/health/live")
    api.get("/api/v1/health/ready")


def section_password(api: Api) -> str:
    heading(
        4,
        "Password login",
        "auth_password",
        "Sign up, sign in with either the username or the address, change the "
        "password, and recover a forgotten one.",
    )
    signup = api.post(
        "/api/v1/auth/password/signup",
        {"identifier": "zoe", "password": PASSWORD, "email": "zoe@example.com"},
    )
    api.sign_in_as(signup["credentials"])

    note("The email address works as the identifier too.")
    login = api.post(
        "/api/v1/auth/password/login",
        {"identifier": "zoe@example.com", "password": PASSWORD},
        token="",
    )

    note("A wrong password is a 401 that says nothing about which half was wrong.")
    api.post(
        "/api/v1/auth/password/login",
        {"identifier": "zoe", "password": "not-the-password"},
        token="",
        expect=401,
    )

    note("Changing the password retires every credential the account had.")
    api.post(
        "/api/v1/auth/password/change",
        {"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        token=login["credentials"]["access_token"],
    )
    api.get("/api/v1/users/me", token=login["credentials"]["access_token"], expect=401)

    note("Forgotten password: the answer is identical whether or not the address exists.")
    forgot = api.post("/api/v1/auth/password/forgot", {"email": "zoe@example.com"}, token="")
    api.post(
        "/api/v1/auth/password/reset",
        {
            "ticket": forgot["ticket"],
            "code": delivered_code("email"),
            "password": PASSWORD,
        },
        token="",
    )

    fresh = api.post(
        "/api/v1/auth/password/login",
        {"identifier": "zoe", "password": PASSWORD},
        token="",
        show=False,
    )
    api.sign_in_as(fresh["credentials"])
    return str(fresh["credentials"]["access_token"])


def section_accounts(api: Api) -> None:
    heading(
        5,
        "Accounts and profiles",
        "accounts",
        "The custom user model every other table points at, and the profile the "
        "login flows enrich as they learn things.",
    )
    api.get("/api/v1/users/me")

    note("PATCH is partial: an omitted field is left alone, an empty string clears it.")
    api.patch(
        "/api/v1/users/me/profile",
        {"display_name": "Zoe Q.", "bio": "Builds APIs.", "timezone": "Europe/Amsterdam"},
    )


def section_notes(api: Api) -> None:
    """The feature app: what a reader adds to the starter, not what it ships."""
    heading(
        6,
        "A feature app of your own",
        "apps.notes",
        "Scaffolded by `manage.py startapi notes --api-version v1`, then filled "
        "in. Two versions, one set of models, and rows scoped to the caller.",
    )
    written = api.post("/api/v1/notes/", {"title": "Groceries", "body": "Oat milk"}, expect=201)
    api.post("/api/v1/notes/", {"title": "Read this first", "pinned": True}, expect=201)

    note("v1 lists whole notes, pinned ones ahead of newer ones.")
    api.get("/api/v1/notes/")

    note("v2 is the same rows through a lighter list, and it can search them.")
    api.get("/api/v2/notes/?q=oat")

    note(
        "Deleted, it reads exactly as another account's note would: 404 rather "
        "than 403, because a 403 tells a stranger the id exists."
    )
    api.delete(f"/api/v1/notes/{written['id']}", show=False)
    api.get(f"/api/v1/notes/{written['id']}", expect=404)


def section_cms(api: Api) -> None:
    """The content app: an editor writes in the admin, a client reads over HTTP."""
    from django.apps import apps as django_apps
    from django.conf import settings

    if not settings.CMS_ENABLED or not django_apps.is_installed("apps.cms"):
        # Optional like every other app here: not named, not installed, nothing
        # to tour. Said out loud rather than skipped silently.
        heading(7, "Content", "apps.cms", "Not installed: DJANGO_CMS_ENABLED is not set.")
        return

    from apps.cms.fields import FieldType
    from apps.cms.models import (
        Field,
        Menu,
        MenuItem,
        Page,
        PageStatus,
        Section,
        SectionPlacement,
        SiteSettings,
    )
    from apps.cms.preview import make_token

    heading(
        7,
        "Content, in two languages",
        "apps.cms",
        "Pages made of sections made of typed fields, a shared section placed "
        "on many pages, and a menu. All of it typed in the admin; the API reads.",
    )
    SiteSettings.objects.create(
        name={"en-us": "Example Co", "fa": "شرکت نمونه"},
        tagline={"en-us": "We make examples"},
        description={"en-us": "Everything this starter ships, in one project."},
        logo="https://cdn.example.com/logo.svg",
        contact={"email": "hello@example.com"},
    )
    home = Page.objects.create(
        name="Home",
        slug="home",
        title={"en-us": "Example Co - Home"},
        status=PageStatus.PUBLISHED,
    )
    hero = Section.objects.create(page=home, name="Hero", slug="hero")
    Field.objects.create(
        section=hero,
        name="Headline",
        slug="headline",
        field_type=FieldType.TEXT,
        required=True,
        values={"en-us": "Welcome", "fa": "خوش آمدید"},
    )
    Field.objects.create(
        section=hero,
        name="Background",
        slug="background",
        field_type=FieldType.IMAGE,
        order=1,
        # A bare URL, the way an editor types one. What comes back is the
        # canonical object, because the value is normalised on the way in.
        values={"en-us": "https://cdn.example.com/hero.jpg"},
    )
    plans = Section.objects.create(page=home, name="Plans", slug="plans", order=1)
    basic = Section.objects.create(page=home, parent=plans, name="Basic", slug="basic")
    Field.objects.create(
        section=basic,
        name="Price",
        slug="price",
        field_type=FieldType.NUMBER,
        values={"en-us": 9},
    )
    about = Page.objects.create(name="About us", slug="about-us", order=1)

    # A library section: written once, placed on whichever pages want it.
    footer = Section.objects.create(page=None, name="Footer", slug="footer", order=99)
    Field.objects.create(
        section=footer,
        name="Small print",
        slug="small-print",
        field_type=FieldType.TEXT,
        values={"en-us": "© Example Co", "fa": "© شرکت نمونه"},
    )
    SectionPlacement.objects.create(page=home, section=footer, order=99)

    menu = Menu.objects.create(name="Main", slug="main")
    MenuItem.objects.create(menu=menu, label={"en-us": "Home", "fa": "خانه"}, page=home)
    MenuItem.objects.create(
        menu=menu, label={"en-us": "Docs"}, url="https://example.com/docs", order=1, new_tab=True
    )

    note("What a menu is built from. `id` is what the detail route takes.")
    api.get("/api/v1/cms/pages", token="")

    note("The site itself: name, tagline, logo, contact, and the languages on offer.")
    api.get("/api/v1/cms/site", token="", show=False)

    note(
        "One page, whole: its own sections and the shared footer in one list, "
        "each field carrying the type of its value."
    )
    api.get("/api/v1/cms/pages/home", token="")

    note(
        "The same page in Persian. The headline is translated; the image is not, "
        "so it falls back rather than leaving a hole in the layout."
    )
    api.get("/api/v1/cms/pages/home?language=fa", token="", show=False)

    note("Navigation is content too, so adding a page to the menu is not a deploy.")
    api.get("/api/v1/cms/menus/main", token="")

    note(
        f"{about.name} is still a draft, so it is a 404 -- until a signed preview "
        "link asks for it by name."
    )
    api.get("/api/v1/cms/pages/about-us", token="", expect=404, show=False)
    api.get(
        f"/api/v1/cms/pages/about-us?preview={make_token('about-us')}",
        token="",
        show=False,
    )

    note("Accept-Language decides for a client that asks for nothing in particular.")
    api.request(
        "GET",
        "/api/v1/cms/pages/home",
        token="",
        show=False,
        headers={"accept-language": "fa"},
    )


def section_notifications(api: Api) -> None:
    """The other feature app: stored notifications, read over HTTP, pushed over a socket."""
    from django.apps import apps as django_apps
    from django.conf import settings

    if not settings.NOTIFICATIONS_ENABLED or not django_apps.is_installed("apps.notifications"):
        # Optional like everything else here, and said out loud rather than
        # skipped silently.
        heading(
            8,
            "Notifications",
            "apps.notifications",
            "Not installed: DJANGO_NOTIFICATIONS_ENABLED is not set.",
        )
        return

    from django.contrib.auth import get_user_model

    from apps.notifications.events import notify_everyone, notify_user
    from apps.notifications.models import Notification

    heading(
        8,
        "Notifications, over HTTP and over a socket",
        "apps.notifications",
        "The history a client reads on open, and the live feed it holds a "
        "connection to. Public before it is authenticated, private after.",
    )

    zoe = get_user_model().objects.get(username="zoe")
    stranger = get_user_model().objects.create_user(username="wren", email="wren@example.com")

    note("Nothing has been said yet, so the badge a client renders is zero.")
    api.get("/api/v1/notifications/unread-count")

    note(
        "There is no endpoint that writes one. A notification is created by the "
        "code that has something to say, so this is a function call, not a POST."
    )
    export = notify_user(zoe, subject="Your export is ready", link="/exports/42")
    notify_everyone(subject="Maintenance at 22:00 UTC", level="warning")
    notify_user(stranger, subject="Somebody mentioned you")
    print(f"  {DIM}│ notify_user(zoe, ...) and notify_everyone(...){OFF}")

    note(
        "Newest first: what was addressed to this account and what was addressed "
        "to everybody, in one list. The third one belongs to another account and "
        "is simply not here -- the queryset starts scoped and no parameter widens it."
    )
    api.get("/api/v1/notifications")

    note("Read state is per account, so a global notification is read by each person separately.")
    api.post(f"/api/v1/notifications/{export.pk}/read")

    note("`?unread=true` is the query a notification tray actually makes on open.")
    api.get("/api/v1/notifications?unread=true", show=False)

    note(
        "Another account's notification is a 404, the same answer an id that "
        "never existed gets: saying which would confirm somebody else's mail."
    )
    theirs = Notification.objects.filter(recipient=stranger).get()
    api.post(f"/api/v1/notifications/{theirs.pk}/read", expect=404, show=False)

    note("Clearing the tray, so the socket below starts from a known count.")
    api.post("/api/v1/notifications/read-all")

    asyncio.run(_notification_sockets(api, zoe))


async def _notification_sockets(api: Api, user: Any) -> None:
    """Three connections, because the socket has three things worth showing."""
    from asgiref.sync import sync_to_async
    from django.conf import settings

    from apps.notifications.events import notify_everyone, notify_user

    path = settings.NOTIFICATIONS_WS_PATH

    note(
        "A connection with no credential at all is accepted, and joins the "
        "channel everybody is on. This is the design, not a fallback: a page "
        "renders announcements to a visitor who never signs in."
    )
    async with Socket(path) as public:
        ready = await public.open()
        if ready["authenticated"]:
            raise WalkthroughError("a socket with no credential reported itself authenticated")
        await sync_to_async(notify_everyone)(subject="Scheduled maintenance", level="warning")
        await public.frame("notification")

    note(
        "The same socket, unlocked mid-connection. `authenticate` adds this "
        "account's own channel to the connection that already had the public "
        "one, so a client opens one socket rather than one per audience."
    )
    async with Socket(path) as private:
        await private.open()
        signed_in = await private.command(
            {"command": "authenticate", "token": api.token}, "authenticated"
        )
        if signed_in["user"]["username"] != user.username:
            raise WalkthroughError("the socket authenticated as the wrong account")

        note(
            f"Authenticating also catches the connection up: {signed_in['unread']} "
            "unread, oldest first, bounded by DJANGO_NOTIFICATIONS_SOCKET_BACKLOG so "
            "that a client away for a year is not sent a year. The list endpoint "
            "above holds the rest of the history."
        )
        for _ in range(signed_in["unread"]):
            await private.frame("notification", show=False)

        note("Now a private one, pushed the moment it is created.")
        mine = await sync_to_async(notify_user)(
            user, subject="Your invoice is ready", link="/invoices/7"
        )
        delivered = await private.frame("notification")
        if delivered["notification"]["id"] != str(mine.pk):
            raise WalkthroughError("the socket delivered a notification nobody asked for")

        note("Reading one over the socket, so a second tab needs no HTTP call.")
        await private.command({"command": "read", "id": str(mine.pk)}, "read")

        note(
            "A refusal is a frame, not a close. A mistyped id should cost one "
            "message, not the connection and everything else flowing over it --"
        )
        await private.command({"command": "read", "id": "not-a-uuid"}, "error")

        note("-- and the proof is that the connection is still answering.")
        await private.command({"command": "ping"}, "pong")

    note(
        "Finally, a credential presented in the handshake. A client that already "
        "knows who it is should not have to wait a round trip, so `?token=` -- "
        "the one a browser can set -- is honoured at connect."
    )
    async with Socket(path, query=f"token={api.token}", label="token") as handshake:
        ready = await handshake.open()
        if not ready["authenticated"]:
            raise WalkthroughError("a token in the query string did not authenticate the socket")
        for _ in range(ready["unread"]):
            await handshake.frame("notification", show=False)


def section_email_code(api: Api) -> None:
    heading(
        9,
        "One-time code by email",
        "auth_email_code",
        "No password at all: a ticket goes to the client, a code goes to the "
        "inbox, and only the pair together is a login.",
    )
    started = api.post(
        "/api/v1/auth/email-code/signup/start",
        {"email": "ravi@example.com"},
        token="",
    )
    signup = api.post(
        "/api/v1/auth/email-code/signup/verify",
        {"ticket": started["ticket"], "code": delivered_code("email")},
        token="",
    )

    note("Signing in again is the same two steps.")
    started = api.post(
        "/api/v1/auth/email-code/login/start", {"email": "ravi@example.com"}, token=""
    )

    note("A wrong code is refused, and the ticket counts the attempts against itself.")
    api.post(
        "/api/v1/auth/email-code/login/verify",
        {"ticket": started["ticket"], "code": "000000"},
        token="",
        expect=400,
    )
    api.post(
        "/api/v1/auth/email-code/login/verify",
        {"ticket": started["ticket"], "code": delivered_code("email")},
        token="",
        show=False,
    )

    note("Logout hands back the credential, which is all the server needs to retire it.")
    api.post(
        "/api/v1/auth/email-code/logout",
        {"token": signup["credentials"]["access_token"]},
        token="",
    )


def section_sms_code(api: Api) -> None:
    heading(
        10,
        "One-time code by SMS",
        "auth_sms_code",
        "The same two steps over a phone number, which is the one identifier "
        "that produces an account with no email address at all.",
    )
    started = api.post("/api/v1/auth/sms-code/signup/start", {"phone": "+14155550101"}, token="")
    code = delivered_code("sms")
    api.post(
        "/api/v1/auth/sms-code/signup/verify",
        {"ticket": started["ticket"], "code": code},
        token="",
    )


def section_magic_link(api: Api) -> None:
    heading(
        11,
        "Magic link",
        "auth_magic_link",
        "One emailed link, good once. The client never sees a code: the token in "
        "the URL is the whole credential.",
    )
    api.post("/api/v1/auth/magic-link/signup/start", {"email": "mika@example.com"}, token="")
    token = delivered_link_token()
    api.post("/api/v1/auth/magic-link/verify", {"token": token}, token="")

    note("Good once: the record is gone, so a replayed link reads as expired.")
    api.post("/api/v1/auth/magic-link/verify", {"token": token}, token="", expect=410)


def section_twofactor(api: Api) -> None:
    heading(
        12,
        "Second factors",
        "auth_twofactor",
        "Four factors on one app. Enrolment is not real until a code confirms "
        "it, so a half-finished setup can never lock an account out.",
    )
    account = api.post(
        "/api/v1/auth/password/signup",
        {"identifier": "quinn", "password": PASSWORD, "email": "quinn@example.com"},
        token="",
        show=False,
    )
    bearer = account["credentials"]["access_token"]

    note("TOTP: the server hands over a secret, the authenticator proves it arrived.")
    enrolled = api.post("/api/v1/auth/2fa/totp/enroll", token=bearer)
    secret = enrolled["secret"]
    api.post("/api/v1/auth/2fa/totp/confirm", {"code": totp_code(secret)}, token=bearer)

    note("SMS and email: enrol, receive a code, confirm.")
    sms = api.post("/api/v1/auth/2fa/sms/enroll", {"phone": "+14155550188"}, token=bearer)
    api.post(
        "/api/v1/auth/2fa/sms/confirm",
        {"ticket": sms["ticket"], "code": delivered_code("sms")},
        token=bearer,
    )
    mail = api.post("/api/v1/auth/2fa/email/enroll", token=bearer)
    api.post(
        "/api/v1/auth/2fa/email/confirm",
        {"ticket": mail["ticket"], "code": delivered_code("email")},
        token=bearer,
    )

    note("Recovery codes are shown once. The server keeps only digests.")
    recovery = api.post("/api/v1/auth/2fa/recovery/generate", token=bearer)
    api.get("/api/v1/auth/2fa/methods", token=bearer)

    note("Now a login is two steps: a ticket instead of a credential.")
    first_step = api.post(
        "/api/v1/auth/password/login",
        {"identifier": "quinn", "password": PASSWORD},
        token="",
    )
    ticket = first_step["login_ticket"]
    api.post(
        "/api/v1/auth/2fa/verify",
        {"login_ticket": ticket, "code": totp_code(secret, 1), "method": "totp"},
        token="",
    )

    note("Or ask for a code on another enrolled factor instead.")
    first_step = api.post(
        "/api/v1/auth/password/login",
        {"identifier": "quinn", "password": PASSWORD},
        token="",
        show=False,
    )
    ticket = first_step["login_ticket"]
    api.post("/api/v1/auth/2fa/challenge", {"login_ticket": ticket, "method": "sms"}, token="")
    api.post(
        "/api/v1/auth/2fa/verify",
        {"login_ticket": ticket, "code": delivered_code("sms"), "method": "sms"},
        token="",
        show=False,
    )

    note("A recovery code works when the phone is gone, and is spent on use.")
    first_step = api.post(
        "/api/v1/auth/password/login",
        {"identifier": "quinn", "password": PASSWORD},
        token="",
        show=False,
    )
    api.post(
        "/api/v1/auth/2fa/verify",
        {
            "login_ticket": first_step["login_ticket"],
            "code": recovery["codes"][0],
            "method": "recovery",
        },
        token="",
        show=False,
    )
    api.delete("/api/v1/auth/2fa/sms", token=bearer)
    api.get("/api/v1/auth/2fa/methods", token=bearer)


def section_tokens(api: Api) -> None:
    from django.conf import settings

    mode = settings.AUTH_TOKEN_MODE
    heading(
        13,
        f"Token mode: {mode}",
        "no token app" if mode == "none" else f"oauth_core, oauth_{mode}",
        "All three modes publish the same endpoints under /auth/token, so a "
        "client does not change when a deployment changes its mind.",
    )
    if mode == "none":
        note(
            "This mode issues a Django session cookie instead of a token, and "
            "publishes no /auth/token endpoints -- there is no credential for a "
            "client to refresh, revoke or enumerate. Name a mode to get one: "
            "DJANGO_AUTH_TOKEN_MODE=rotation."
        )
        return
    laptop = api.post(
        "/api/v1/auth/password/login",
        {"identifier": "zoe", "password": PASSWORD},
        token="",
        show=False,
    )
    api.sign_in_as(laptop["credentials"])
    phone = api.post(
        "/api/v1/auth/password/login",
        {"identifier": "zoe", "password": PASSWORD},
        token="",
        show=False,
    )

    note("Every live credential for the account, as a device list would show it.")
    listed = api.get("/api/v1/auth/token/sessions")

    note("Refresh. In sliding mode the body is empty and the header carries it.")
    refreshed = api.post(
        "/api/v1/auth/token/refresh",
        {"refresh_token": laptop["credentials"].get("refresh_token", "")},
        token=laptop["credentials"]["access_token"],
    )
    api.sign_in_as(refreshed)

    note("End another session by id -- scoped to the caller's own account.")
    others = [
        item
        for item in listed["sessions"]
        if item["session_id"] != phone["credentials"]["session_id"]
    ]
    if others:
        api.delete(f"/api/v1/auth/token/sessions/{others[0]['session_id']}")

    note("Revoke the presented credential, and it stops working immediately.")
    api.post("/api/v1/auth/token/revoke", {"token": api.token})
    api.get("/api/v1/users/me", expect=401, show=False)
    api.token = phone["credentials"]["access_token"]


def section_social(api: Api) -> None:
    from django.conf import settings

    heading(
        14,
        "Social sign-in",
        ", ".join(f"oauth_{name}" for name in settings.OAUTH_PROVIDERS),
        "Each provider mounts a start and a callback. Start is the half this "
        "project owns: state, PKCE and a redirect the browser follows.",
    )
    for provider in settings.OAUTH_PROVIDERS:
        api.get(f"/api/v1/oauth/{provider}/start", expect=302, token="", show=False)
    note(
        "The callback is answered by the provider against a real client id, so it "
        "is the one thing an offline tour cannot show. docs/oauth/ has the setup "
        "for each, including why Apple posts its callback instead of redirecting."
    )


def section_audit(api: Api) -> None:
    from infrastructure.auth.core.models import AuthEvent
    from infrastructure.oauth.core import jwt_tokens

    heading(
        15,
        "What was recorded",
        "auth_core, oauth_core",
        "Every step above left an audit row, and every credential above was a "
        "signed JWT whose only secret is the handle its database row is keyed by.",
    )
    counts: dict[str, int] = {}
    for event in AuthEvent.objects.all():
        counts[f"{event.method or '-'} · {event.event_type}"] = (
            counts.get(f"{event.method or '-'} · {event.event_type}", 0) + 1
        )
    for label, count in sorted(counts.items()):
        print(f"  {count:>3} × {label}")

    if not api.token:
        note("The `none` token mode signs its callers in with a cookie, so there is no JWT here.")
        return

    note("The claims a resource server can check before touching the database:")
    claims = jwt_tokens.decode(api.token, token_type=jwt_tokens.ACCESS)
    print(f"  subject    {claims.subject}")
    print(f"  mode       {claims.mode}")
    print(f"  methods    {', '.join(claims.methods)}")
    print(f"  session_id {claims.session_id}")
    print(f"  expires_at {claims.expires_at.isoformat()}")
    print(f"  jti        {shorten(claims.handle)}  {DIM}(hashed in the database){OFF}")


def section_openapi(api: Api) -> None:
    heading(
        16,
        "The document all of that produced",
        "config.api",
        "One NinjaAPI per registered version, every enabled app's router "
        "attached to it. Swagger is at /api/docs with a selector for both.",
    )
    from infrastructure.common.registry import load_api_registry

    for version in load_api_registry():
        schema = api.get(f"/api/{version}/openapi.json", token="", show=False)
        by_tag: dict[str, int] = {}
        for operations in schema["paths"].values():
            for operation in operations.values():
                for tag in operation.get("tags", ["untagged"]):
                    by_tag[tag] = by_tag.get(tag, 0) + 1
        total = sum(by_tag.values())
        # In the document's own order, which is the order Swagger renders the
        # groups in, with the line it prints under each heading.
        for tag in schema["tags"]:
            count = by_tag.pop(tag["name"], 0)
            blurb = textwrap.shorten(tag.get("description", ""), width=40, placeholder="…")
            print(f"  {count:>3} ops  {tag['name']:<19} {DIM}{blurb}{OFF}")
        for name, count in sorted(by_tag.items()):
            # A group Swagger would render with no heading text and no place in
            # the order. Nothing should reach here; if it does, say so.
            print(f"  {count:>3} ops  {name:<19} {RED}undeclared tag{OFF}")
        print(f"  {BOLD}{total} operations across {len(schema['paths'])} paths{OFF}")
        schemes = ", ".join(schema["components"].get("securitySchemes", {}))
        print(f"  {DIM}{version}: security schemes: {schemes}{OFF}\n")
    note(
        "v2 carries only the routers registered for it plus the ones every "
        "version shares, which is why the notes app appears in both documents "
        "and the health router in one."
    )


def tour() -> int:
    """The tour itself, running inside the example project."""
    configure()
    api = Api()
    print(f"\n{BOLD}Django Ninja Starter — every app, end to end{OFF}")
    print(f"{DIM}In-process against an in-memory database. Nothing here touches your project.{OFF}")
    try:
        section_configuration()
        section_checks()
        section_health(api)
        section_password(api)
        section_accounts(api)
        section_notes(api)
        section_cms(api)
        section_notifications(api)
        section_email_code(api)
        section_sms_code(api)
        section_magic_link(api)
        section_twofactor(api)
        section_tokens(api)
        section_social(api)
        section_audit(api)
        section_openapi(api)
    except WalkthroughError as error:
        print(f"\n{RED}The tour stopped: {error}{OFF}")
        return 1
    print(f"\n{GREEN}{BOLD}Done.{OFF} Every installed app answered.\n")
    return 0


def main() -> int:
    """Build the example project if it is missing, then tour it in a child."""
    if os.environ.get(IN_PROJECT):
        return tour()

    parser = argparse.ArgumentParser(
        prog="examples/walkthrough.py",
        description="Tour every app of the example project this starter generates.",
    )
    parser.add_argument(
        "--project",
        type=Path,
        default=build.DEFAULT_DESTINATION,
        help=f"the project to tour (defaults to {build.DEFAULT_DESTINATION.name}, built on demand)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="rebuild the project from the current template before touring it",
    )
    arguments = parser.parse_args()

    try:
        project = build.ensure(arguments.project, rebuild=arguments.rebuild)
    except (FileExistsError, RuntimeError) as error:
        print(f"{RED}The example project could not be built: {error}{OFF}", file=sys.stderr)
        return 1
    return relaunch_inside(project)


if __name__ == "__main__":
    raise SystemExit(main())
