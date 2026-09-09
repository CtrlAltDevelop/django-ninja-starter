#!/usr/bin/env python3
"""Exercise every app this starter ships, in a single run.

    python examples/walkthrough.py

Four login methods, four second factors, four social providers, a token mode,
the accounts and health endpoints, all three feature apps the starter ships --
the CMS, notifications with its socket, and the shop from catalogue to settled
invoice -- the notes app you would write yourself, the audit trail, the
generated OpenAPI documents, and every admin screen any of them registers. All
of it printed as a transcript of the calls a real client would make.

The admin is the last section and is not an afterthought. The API is half of
what this project is; the other half is the screen the people who run it use,
and it is walked from Django's own registry rather than from a list written
here -- so an app added tomorrow is covered without this file being edited, and
one that ships a broken changelist fails the tour.

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
from typing import Any, ClassVar

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
        # Every path this tour has asked for, so a section can assert it left
        # nothing out. A transcript that quietly stops covering an endpoint is
        # how a tour comes to describe an app it no longer exercises.
        self.visited: set[tuple[str, str]] = set()

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
        files: dict[str, tuple[str, bytes]] | None = None,
    ) -> Any:
        self.visited.add((method.upper(), path.split("?")[0]))
        bearer = self.token if token is None else token
        extra: dict[str, Any] = {"HTTP_AUTHORIZATION": f"Bearer {bearer}"} if bearer else {}
        if headers:
            extra["headers"] = headers
        send = getattr(self.client, method.lower())
        if files is not None:
            # Multipart rather than JSON, because an upload endpoint takes a
            # file and there is no way to put one in a JSON body. Django's test
            # client picks the encoding from the absence of `content_type`.
            from django.core.files.uploadedfile import SimpleUploadedFile

            body_files = {
                field: SimpleUploadedFile(name, content) for field, (name, content) in files.items()
            }
            response = send(path, {**(payload or {}), **body_files}, **extra)
        elif payload is None:
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

    def put(self, path: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.request("PUT", path, payload if payload is not None else {}, **kwargs)

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

    #: Every command name any connection in this tour has sent. Shared across
    #: instances because a conversation is two connections and the coverage
    #: question is about the socket, not about either end of it.
    sent: ClassVar[set[str]] = set()

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

    async def frame(
        self, expect: str, *, show: bool = True, patient: bool = False
    ) -> dict[str, Any]:
        """Read one server frame, insisting it is the one the tour says it is.

        ``patient`` reads past anything else that arrives first. One command
        can legitimately produce several frames -- a reply, the thread's own
        update, a read receipt -- and a connection belonging to an agent is
        joined to the desk's channel as well as to its own conversations. A
        transcript that wants one of those frames should not have to know the
        order the others happen to arrive in.
        """
        message = await self._next_message()
        if patient:
            while message["type"] == "websocket.send":
                if json.loads(message["text"]).get("type") == expect:
                    break
                print(f"  {DIM}│ (also sent {json.loads(message['text']).get('type')}){OFF}")
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
        self, command: dict[str, Any], expect: str, *, show: bool = True, patient: bool = False
    ) -> dict[str, Any]:
        """Send one command and read the frame it is answered with.

        ``patient`` reads past anything else that arrives first. A connection
        belonging to an agent is joined to the desk's channel as well as to its
        own conversations, so it is told about a thread changing at the same
        time as it is answered -- which is right for a client and unhelpful for
        a transcript that wants the answer to the command it just sent.
        """
        Socket.sent.add(command["command"])
        print(f"  {DIM}│{OFF} {CYAN}→ {command['command']}{OFF}")
        await self._to_server.put({"type": "websocket.receive", "text": json.dumps(command)})
        return await self.frame(expect, show=show, patient=patient)


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
    print(f"  shop             {'on' if settings.SHOP_ENABLED else 'off'}")
    support = "off"
    if settings.SUPPORT_ENABLED:
        support = f"on, socket at {settings.SUPPORT_WS_PATH}"
    print(f"  support          {support}")
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
        # What a link to this site becomes when somebody shares it. Separate
        # from the pair above because the good version of each is different: a
        # page title is read next to the site's chrome, a card is read alone.
        og_title={"en-us": "Example Co — every app, in one project"},
        og_image="https://cdn.example.com/card.png",
        og_url="https://example.com/",
        logo="https://cdn.example.com/logo.svg",
        contact={"email": "hello@example.com"},
    )
    home = Page.objects.create(
        name="Home",
        slug="home",
        title={"en-us": "Example Co - Home"},
        description={"en-us": "Everything this starter ships."},
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
    # One field of each remaining family, because the admin's whole promise is
    # that a type changes what an editor types into -- and a page with three
    # text fields on it never demonstrates that.
    Field.objects.create(
        section=hero,
        name="Standfirst",
        slug="standfirst",
        field_type=FieldType.TEXTAREA,
        order=2,
        values={"en-us": "Four login methods, three feature apps, one project."},
    )
    Field.objects.create(
        section=hero,
        name="Width",
        slug="width",
        field_type=FieldType.SELECT,
        options=[{"value": "full", "label": "Full bleed"}, {"value": "boxed", "label": "Boxed"}],
        order=3,
        values={"en-us": "full"},
    )
    Field.objects.create(
        section=hero,
        name="Accent",
        slug="accent",
        field_type=FieldType.COLOR,
        order=4,
        values={"en-us": "#2563EB"},
    )
    Field.objects.create(
        section=hero,
        name="Gallery",
        slug="gallery",
        field_type=FieldType.IMAGE,
        multiple=True,
        order=5,
        values={"en-us": ["https://cdn.example.com/one.jpg", "https://cdn.example.com/two.jpg"]},
    )
    Field.objects.create(
        section=hero,
        name="Read more",
        slug="read-more",
        field_type=FieldType.PAGE,
        order=6,
        # Another page by name, not by address: a client routes its own pages,
        # and renaming the slug is the one thing that visibly breaks this.
        values={"en-us": "about-us"},
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
    note(
        "The colour is stored lower-cased, the choice is checked against that "
        "field's own options, and the gallery is a list of canonical image "
        "objects -- all of it normalised on the way in, so a client switches on "
        "`type` and knows exactly what it has."
    )

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

    note(
        "The site itself: name, tagline, logo, contact, the languages on offer, "
        "and the Open Graph card a link to it becomes. No meta keywords -- "
        "nothing has read them for over a decade."
    )
    api.get("/api/v1/cms/site", token="", show=False)

    note(
        "One page, whole: its own sections and the shared footer in one list, "
        "each field carrying the type of its value. `meta` arrives with the "
        "site's values already merged behind the page's own, so a client renders "
        "a <head> without a fallback chain of its own."
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
            "A change is also announced to this account's *other* connections, "
            "so a badge cleared on a phone clears on the laptop. This connection "
            "hears its own announcement too -- redundant rather than wrong, since "
            "applying it twice is a no-op."
        )
        await private.frame("state")

        note(
            "The socket does everything the endpoints do, so a client holding "
            "one open needs no HTTP client beside it to render its tray."
        )
        listed = await private.command({"command": "list", "limit": 3}, "list", show=False)
        print(f"  {DIM}│ {len(listed['notifications'])} of {listed['total']}{OFF}")

        note(
            "Dismissing takes it out of this account's tray -- and never deletes "
            "it, because a broadcast belongs to everybody."
        )
        await private.command({"command": "dismiss", "id": str(mine.pk)}, "dismiss", show=False)
        await private.frame("state", show=False)

        note(
            "A refusal is a frame, not a close. A mistyped id should cost one "
            "message, not the connection and everything else flowing over it --"
        )
        await private.command({"command": "read", "id": "not-a-uuid"}, "error")

        note("-- and the proof is that the connection is still answering.")
        await private.command({"command": "ping"}, "pong")

        note(
            "Signing out keeps the connection and the public feed, and puts the "
            "private commands back behind a credential. A shared browser should "
            "stop seeing one person's mail without losing the announcements."
        )
        await private.command({"command": "deauthenticate"}, "deauthenticated", show=False)
        await private.command({"command": "unread"}, "error", show=False)

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


def section_shop(api: Api) -> None:
    """The third feature app: a catalogue anyone may read, a basket only you may fill."""
    from django.apps import apps as django_apps
    from django.conf import settings

    if not settings.SHOP_ENABLED or not django_apps.is_installed("apps.shop"):
        # Optional like every other app here, and said out loud rather than
        # skipped silently.
        heading(9, "Shop", "apps.shop", "Not installed: DJANGO_SHOP_ENABLED is not set.")
        return

    from decimal import Decimal

    from django.utils import timezone

    from apps.shop.attributes import AttributeType
    from apps.shop.models import (
        Brand,
        Category,
        CategoryAttribute,
        Collection,
        CollectionItem,
        Coupon,
        Discount,
        DiscountKind,
        Product,
        ProductAttribute,
        ProductImage,
        ProductOffer,
        ProductStatus,
        ProductVariant,
        Seller,
        ShippingMethod,
        Tag,
    )

    heading(
        9,
        "A shop, from the catalogue to a settled invoice",
        "apps.shop",
        "Two halves governed differently: a catalogue anybody may read without "
        "a credential, and a basket, an order and an invoice that are nobody's "
        "but the caller's.",
    )

    # The catalogue an editor would build in the admin. Deliberately not
    # minimal: half of what this app does only shows up when there is a tree
    # rather than a category, a product with variants beside one without, and a
    # second seller undercutting the shop on its own listing.
    electronics = Category.objects.create(name="Electronics", slug="electronics", order=0)
    laptops = Category.objects.create(name="Laptops", slug="laptops", parent=electronics, order=0)
    CategoryAttribute.objects.create(
        category=laptops,
        name="Screen size",
        code="screen-size",
        attribute_type=AttributeType.NUMBER,
        unit="in",
        required=True,
    )
    shirts = Category.objects.create(name="Shirts", slug="shirts", order=1)
    CategoryAttribute.objects.create(
        category=shirts,
        name="Size",
        code="size",
        attribute_type=AttributeType.CHOICE,
        choices=["S", "M", "L"],
        is_variant=True,
    )

    acme = Brand.objects.create(name="Acme", slug="acme", website="https://acme.example.com")
    store = Seller.objects.create(name="Acme Store", slug="acme-store", city="Bristol", order=0)
    bargains = Seller.objects.create(name="Bargain Bin", slug="bargain-bin", city="Leeds", order=1)

    laptop = Product.objects.create(
        category=laptops,
        brand=acme,
        seller=store,
        name="Acme Featherbook 14",
        slug="featherbook-14",
        subtitle="Thin, light, quiet",
        summary="A small laptop for writing on trains.",
        sku="FB-14",
        price=Decimal("1200.00"),
        status=ProductStatus.ACTIVE,
        stock=5,
        is_featured=True,
        sales_count=40,
    )
    laptop.tags.add(Tag.objects.create(name="Portable", slug="portable"))
    ProductImage.objects.create(
        product=laptop, url="https://cdn.example.com/fb14.jpg", alt="A laptop", is_primary=True
    )
    ProductAttribute.objects.create(
        product=laptop, attribute=laptops.attributes.get(code="screen-size"), value=14
    )
    # A second seller, cheaper and in stock: the buy box has a decision to make.
    ProductOffer.objects.create(
        product=laptop, seller=bargains, price=Decimal("1100.00"), stock=2, lead_time_days=2
    )
    # A sibling in the same category, so the row under a product page has
    # something in it rather than proving only that the endpoint answers.
    Product.objects.create(
        category=laptops,
        brand=acme,
        seller=store,
        name="Acme Featherbook 16",
        slug="featherbook-16",
        summary="The same laptop, two inches wider.",
        sku="FB-16",
        price=Decimal("1500.00"),
        status=ProductStatus.ACTIVE,
        stock=2,
        sales_count=11,
    )
    tee = Product.objects.create(
        category=shirts,
        name="Plain tee",
        slug="plain-tee",
        summary="A shirt with nothing on it.",
        sku="TEE",
        price=Decimal("20.00"),
        status=ProductStatus.ACTIVE,
        has_variants=True,
    )
    ProductVariant.objects.create(product=tee, sku="TEE-M", options={"size": "M"}, stock=3)
    ProductVariant.objects.create(
        product=tee, sku="TEE-L", options={"size": "L"}, price=Decimal("22.00"), stock=0, order=1
    )
    Product.objects.create(
        category=laptops,
        name="Unannounced thing",
        slug="unannounced",
        sku="SECRET",
        price=Decimal("99.00"),
        status=ProductStatus.DRAFT,
        stock=1,
    )

    sale = Discount.objects.create(
        name="Spring sale",
        kind=DiscountKind.PERCENT,
        value=Decimal("10.00"),
        starts_at=timezone.now() - timezone.timedelta(days=1),
        ends_at=timezone.now() + timezone.timedelta(days=1),
    )
    sale.products.add(laptop)
    picks = Collection.objects.create(
        name="Staff picks", slug="staff-picks", description="What we would buy."
    )
    CollectionItem.objects.create(collection=picks, product=laptop, order=0)
    Coupon.objects.create(code="WELCOME", percent=Decimal("5.00"))
    shipping = ShippingMethod.objects.create(
        name="Standard", price=Decimal("5.00"), free_from=Decimal("2000.00")
    )
    note("The category tree, with a count on each node. No credential anywhere here.")
    api.get("/api/v1/shop/categories", token="")

    note("And one node of it alone, which is what a category page opens with.")
    api.get("/api/v1/shop/categories/laptops", token="", show=False)

    note("The two other axes a shopper narrows by before searching at all.")
    api.get("/api/v1/shop/brands", token="", show=False)
    api.get("/api/v1/shop/sellers", token="", show=False)

    note(
        "One endpoint answers search, a category page and every filter on it. "
        "A category includes everything underneath it, so `electronics` finds "
        "the laptop filed under `electronics/laptops`."
    )
    api.get("/api/v1/shop/products?category=electronics&sort=price_low", token="", show=False)

    note("Attribute filters read `code:value` and are repeatable; every one has to match.")
    api.get("/api/v1/shop/products?attribute=screen-size:14", token="", show=False)

    note("The draft is in the table and in nothing public.")
    api.get("/api/v1/shop/products/unannounced", token="", expect=404, show=False)

    note(
        "One product: its price after the running discount, the sellers who can "
        "fill it, and which of them wins the buy box."
    )
    api.get("/api/v1/shop/products/featherbook-14", token="")

    note(
        "The row every product page carries under it: the same category, best "
        "rated first, and never the product being looked at."
    )
    api.get("/api/v1/shop/products/featherbook-14/related", token="", show=False)

    note(
        "A product sold in variants answers the picker's two questions at once: "
        "which sizes exist, and which of them are still buyable -- here, the "
        "large is made but nobody is holding one. Each size also carries its own "
        "sellers, its own shelf and every seller's."
    )
    api.get("/api/v1/shop/products/plain-tee", token="")

    note("Named lists are filters over the live catalogue, so none of them can go stale.")
    api.get("/api/v1/shop/listings", token="", show=False)
    api.get("/api/v1/shop/listings/bestsellers", token="", show=False)
    api.get("/api/v1/shop/collections", token="", show=False)
    api.get("/api/v1/shop/collections/staff-picks", token="", show=False)
    api.get("/api/v1/shop/sellers/bargain-bin", token="", show=False)

    note("A view is recorded without a credential; popularity is a public fact.")
    api.post("/api/v1/shop/products/featherbook-14/view", token="", show=False)

    note(
        "A basket is scratch paper before it is an order. A line can be dropped "
        "and the whole thing tipped out, and either way what comes back is the "
        "basket as it now stands rather than a bare 204 to go and re-read."
    )
    scratch = api.post("/api/v1/shop/cart/items", {"product": "featherbook-16"}, show=False)
    api.delete(f"/api/v1/shop/cart/items/{scratch['items'][0]['id']}", show=False)
    api.post("/api/v1/shop/cart/items", {"product": "featherbook-16"}, show=False)
    api.delete("/api/v1/shop/cart", show=False)

    note("The basket needs one. Without a seller named, it takes the one the page showed.")
    api.post("/api/v1/shop/cart/items", {"product": "featherbook-14", "quantity": 1})

    note("A variant product is bought through a variant, never through the product.")
    api.post(
        "/api/v1/shop/cart/items",
        {"product": "plain-tee", "variant": str(tee.variants.get(sku="TEE-M").pk), "quantity": 2},
        show=False,
    )

    note("More than the shelf holds is refused, with the number that is left.")
    api.post(
        "/api/v1/shop/cart/items",
        {"product": "plain-tee", "variant": str(tee.variants.get(sku="TEE-L").pk)},
        expect=400,
        show=False,
    )

    cart = api.get("/api/v1/shop/cart")

    note("Quantities change on the line, and the basket re-totals itself.")
    api.patch(f"/api/v1/shop/cart/items/{cart['items'][0]['id']}", {"quantity": 2}, show=False)

    note("Likes and reviews are the shopper's own opinions, one per product.")
    api.put("/api/v1/shop/products/featherbook-14/like", show=False)
    api.post(
        "/api/v1/shop/products/featherbook-14/reviews",
        {"rating": 5, "title": "Quiet", "body": "Wrote a book on it."},
        show=False,
    )
    note(
        "Moderation is on by default, so it is not public yet -- and the author "
        "can still see their own."
    )
    api.get("/api/v1/shop/products/featherbook-14/reviews", token="", show=False)
    api.get("/api/v1/shop/reviews/mine", show=False)
    api.get("/api/v1/shop/favourites", show=False)

    note("Both are the shopper's to take back, and taking one back is not an error.")
    api.put("/api/v1/shop/products/plain-tee/like", show=False)
    api.delete("/api/v1/shop/products/plain-tee/like", show=False)
    api.post(
        "/api/v1/shop/products/plain-tee/reviews",
        {"rating": 3, "title": "Plain", "body": "It is a shirt."},
        show=False,
    )
    api.delete("/api/v1/shop/products/plain-tee/reviews", show=False)

    note(
        "Checkout needs an address and a delivery option, and the address book "
        "is the caller's own -- written, read, corrected and defaulted through "
        "the API rather than seeded behind it."
    )
    address = api.post(
        "/api/v1/shop/addresses",
        {
            "full_name": "Zoe Example",
            "phone": "+441234567890",
            "country": "GB",
            "city": "Bristol",
            "postal_code": "BS1 4ST",
            "line1": "1 Example Street",
        },
    )
    api.get("/api/v1/shop/addresses", show=False)
    api.get(f"/api/v1/shop/addresses/{address['id']}", show=False)
    api.patch(f"/api/v1/shop/addresses/{address['id']}", {"line2": "Flat 2"}, show=False)
    api.put(f"/api/v1/shop/addresses/{address['id']}/default", show=False)

    note("A second one, saved and then forgotten again.")
    spare = api.post(
        "/api/v1/shop/addresses",
        {
            "label": "Work",
            "full_name": "Zoe Example",
            "phone": "+441234567890",
            "country": "GB",
            "city": "Bath",
            "postal_code": "BA1 1AA",
            "line1": "2 Example Road",
        },
        show=False,
    )
    api.delete(f"/api/v1/shop/addresses/{spare['id']}", show=False)

    note(
        "Delivery options are public, so a shopper comparing them need not have "
        "signed in -- but a caller who has gets each one costed against what is "
        "actually in the basket, which is the only way a free-over threshold can "
        "be printed honestly."
    )
    api.get("/api/v1/shop/shipping-methods")

    note(
        "A coupon is tried before it is committed to. A code the shop will not "
        "take comes back as an answer saying why, not as a 400 per keystroke on "
        "a checkout page somebody is still typing into."
    )
    api.post("/api/v1/shop/cart/coupon", {"code": "WELCOME"}, show=False)
    api.post("/api/v1/shop/cart/coupon", {"code": "NOT-A-COUPON"})

    note(
        "Checkout is one atomic step: it prices the basket, holds the stock, "
        "writes an immutable order and issues its invoice."
    )
    order = api.post(
        "/api/v1/shop/checkout",
        {
            "address": address["id"],
            "shipping_method": str(shipping.pk),
            "coupon": "WELCOME",
        },
    )

    note("The basket is empty afterwards: what was in it is on the order now.")
    api.get("/api/v1/shop/cart", show=False)

    note(
        "The invoice is a document, not a view of the order: the address, the "
        "prices and the totals are copied, so editing either afterwards cannot "
        "rewrite what somebody was charged."
    )
    api.get(f"/api/v1/shop/orders/{order['number']}/invoice")

    note(
        "This starter wires up no gateway. Payments are created against the "
        "`manual` provider, and this is the seam a real callback is pointed at."
    )
    api.post(
        f"/api/v1/shop/orders/{order['number']}/payment/confirm",
        {"reference": "bank-statement-4417"},
        show=False,
    )
    api.get(f"/api/v1/shop/orders/{order['number']}", show=False)

    note("And the whole shelf of them, newest first, which is what an account page shows.")
    api.get("/api/v1/shop/orders", show=False)

    note("An order that has been paid for can no longer be cancelled.")
    api.post(f"/api/v1/shop/orders/{order['number']}/cancel", expect=400, show=False)

    note("And somebody else's order number is a 404, not a 403.")
    api.get("/api/v1/shop/orders/S00000000XXXX0000", expect=404, show=False)


def section_support(api: Api) -> None:
    """The fourth feature app: one conversation, seen from both sides of a desk."""
    from django.apps import apps as django_apps
    from django.conf import settings

    if not settings.SUPPORT_ENABLED or not django_apps.is_installed("apps.support"):
        heading(
            10,
            "Support",
            "apps.support",
            "Not installed: DJANGO_SUPPORT_ENABLED is not set.",
        )
        return

    from django.contrib.auth import get_user_model

    from apps.support.models import CannedReply, Category, Tag

    heading(
        10,
        "A support desk, from both sides of it",
        "apps.support",
        "A ticket is a conversation, which is what lets live chat and a filed "
        "problem be one app. Everything below is the same account's token "
        "answering as a client, and an agent's answering as the desk.",
    )

    agatha = get_user_model().objects.create_user(
        username="agatha", email="agatha@example.com", is_staff=True
    )
    desk = desk_token(agatha)

    note(
        "The desk's own furniture, made the way an operator would: a category "
        "carrying the promise, a tag, and a reply somebody says often."
    )
    billing = Category.objects.create(
        name="Billing",
        description="Invoices, payments and refunds.",
        first_response_minutes=60,
        resolution_minutes=60 * 24,
    )
    Category.objects.create(name="General")
    Tag.objects.create(name="Escalated", colour="#dc2626")
    CannedReply.objects.create(
        title="Asking for an invoice number",
        body="Could you send us the invoice number from the email?",
    )
    print(f"  {DIM}│ 2 categories, 1 tag, 1 saved reply{OFF}")

    note(
        "What a client is offered when they file something. The response times "
        "are a promise the desk is making, so they are public to anybody "
        "signed in rather than an internal target."
    )
    api.get("/api/v1/support/categories")

    note(
        "The rest of that furniture is the desk's own and is answered for "
        "staff only: a tag is what the desk says about a thread, not what the "
        "client is told, and fetching a saved reply counts it, which is what "
        "tells an operator which ones are worth keeping."
    )
    api.get("/api/v1/support/tags", token=desk, show=False)
    api.get("/api/v1/support/canned-replies", token=desk)

    note(
        "Opening a ticket. The subject and the category are what make it a "
        "ticket rather than a chat -- and the category is where the SLA "
        "deadlines below come from."
    )
    ticket = api.post(
        "/api/v1/support",
        {
            "kind": "ticket",
            "subject": "I was charged twice",
            "body": "There are two charges on the 3rd, both for $49.",
            "category": billing.slug,
        },
        expect=201,
    )
    ticket_id = ticket["id"]

    note(
        f"The reference {ticket['reference']} is the short string somebody "
        "reads down a telephone. The id is what every other call takes. Note "
        "the SLA: two deadlines written now, never a breach flag set later."
    )

    note(
        "A file goes up on its own, before the message that carries it. This "
        "is the one half of the app that has to be HTTP: a WebSocket frame is "
        "JSON and cannot carry a multipart body."
    )
    upload = api.post(
        "/api/v1/support/uploads",
        None,
        expect=201,
        show=False,
        files={"file": ("statement.txt", b"03/09 -49.00\n03/09 -49.00\n")},
    )
    print(f"  {DIM}│ staged {upload['name']} as {shorten(upload['id'], 12)}{OFF}")

    note("And the message that claims it, sent over HTTP here and over the socket below.")
    said = api.post(
        f"/api/v1/support/{ticket_id}/messages",
        {"body": "Here is the statement.", "upload_ids": [upload["id"]]},
        expect=201,
        show=False,
    )

    note(
        "Rewriting it is the author's alone -- staff get no exception, because "
        "editing what somebody else is recorded as having said is not "
        "moderation. The desk that needs a message gone has the retraction "
        "below, which leaves a tombstone saying so."
    )
    api.patch(
        f"/api/v1/support/messages/{said['id']}",
        {"body": "Here is the statement -- both charges are on page 2."},
        show=False,
    )

    note(
        "The desk's queue is the same endpoint, answering a different question "
        "because a different account is asking. Nothing here takes an account "
        "id, so no parameter widens what a client can see."
    )
    api.get("/api/v1/support?unassigned=true", token=desk)

    note("An agent takes it, and moves it up the queue.")
    api.post(f"/api/v1/support/{ticket_id}/claim", token=desk, show=False)
    api.post(f"/api/v1/support/{ticket_id}/priority", {"priority": "high"}, token=desk, show=False)
    api.post(f"/api/v1/support/{ticket_id}/tags", {"tags": ["escalated"]}, token=desk, show=False)

    note(
        "Billing is somebody else's, so it is handed on. Who a complaint has "
        "been passed between is recorded as an internal event: telling the "
        "client answers a question they did not ask with something that reads "
        "as an apology."
    )
    bruno = get_user_model().objects.create_user(
        username="bruno", email="bruno@example.com", is_staff=True
    )
    api.post(
        f"/api/v1/support/{ticket_id}/assign", {"agent": str(bruno.pk)}, token=desk, show=False
    )

    note(
        "A third person joins the same thread as an observer. An observer who "
        "is not staff reads the public half of it, exactly as the client does."
    )
    dara = get_user_model().objects.create_user(username="dara", email="dara@example.com")
    api.post(
        f"/api/v1/support/{ticket_id}/participants",
        {"account": str(dara.pk), "role": "observer"},
        token=desk,
        expect=201,
        show=False,
    )

    note(
        "Typing is published to whoever is in the thread and never stored. It "
        "is offered over HTTP too, so a client polling one transport is not a "
        "client missing half the app."
    )
    api.post(f"/api/v1/support/{ticket_id}/typing", {"typing": True}, token=desk, show=False)

    note(
        "A staff-only note goes into the same thread, in the order it was "
        "written. A thread whose notes live somewhere else is a thread nobody "
        "reads in order."
    )
    api.post(
        f"/api/v1/support/{ticket_id}/notes",
        {"body": "Duplicate charge confirmed in the gateway. Refunding."},
        token=desk,
        expect=201,
        show=False,
    )

    note("The desk sees it. The client asks for the same thread and simply does not.")
    theirs = api.get(f"/api/v1/support/{ticket_id}/messages", token=desk, show=False)
    hers = api.get(f"/api/v1/support/{ticket_id}/messages", show=False)
    print(f"  {DIM}│ desk: {theirs['total']} messages · client: {hers['total']}{OFF}")
    if theirs["total"] == hers["total"]:
        raise WalkthroughError("the client was shown the desk's internal note")

    note("The reply the client is meant to see.")
    api.post(
        f"/api/v1/support/{ticket_id}/messages",
        {"body": "Confirmed -- the second charge is refunded, 3-5 working days."},
        token=desk,
        expect=201,
        show=False,
    )

    note(
        "Retracting leaves a tombstone rather than removing the row, and is "
        "answered with the message instead of with nothing -- every reader has "
        "it on screen and has to be told what it became."
    )
    api.delete(f"/api/v1/support/messages/{said['id']}", show=False)

    note(
        "Moving it without settling it. `pending` and `on_hold` are statements "
        "about what the desk is doing and are the desk's alone to make; the "
        "client's own verbs are the three below."
    )
    api.post(f"/api/v1/support/{ticket_id}/status", {"status": "pending"}, token=desk, show=False)

    note(
        "The badge, counted per participant rather than per message: one row "
        "carrying a watermark, not a receipt for every line ever written."
    )
    api.get("/api/v1/support/unread")

    note(
        "Marking read never moves the watermark backwards, and does not move "
        "it at all when there was nothing unread -- which is what lets a "
        "scroll handler call this as often as it likes."
    )
    api.post(f"/api/v1/support/{ticket_id}/read")
    api.post(f"/api/v1/support/{ticket_id}/read", show=False)

    note(
        "Putting it back is the one way the watermark does move backwards, and "
        "it drops it entirely rather than by a message: `mark as unread` means "
        "the whole thread is waiting again, which is what somebody clicking it "
        "is asking for."
    )
    api.post(f"/api/v1/support/{ticket_id}/unread", show=False)
    api.post(f"/api/v1/support/{ticket_id}/read", show=False)

    note("Somebody else's conversation is a 404, the same answer an id that never existed gets.")
    stranger = get_user_model().objects.create_user(username="colin", email="colin@example.com")
    api.get(f"/api/v1/support/{ticket_id}", token=desk_token(stranger), expect=404, show=False)

    note("And the desk's verbs are refused for a client, with FORBIDDEN rather than a 404.")
    api.post(f"/api/v1/support/{ticket_id}/claim", expect=403, show=False)

    note(
        "Settled by the client -- and reopened by them, which is the client's "
        "right of reply to being told a thing is finished. Then settled again."
    )
    api.post(f"/api/v1/support/{ticket_id}/close", show=False)
    api.post(f"/api/v1/support/{ticket_id}/reopen", show=False)
    api.post(f"/api/v1/support/{ticket_id}/close", show=False)

    note("Rated -- which only the client may do, and only once it is settled.")
    api.post(f"/api/v1/support/{ticket_id}/rating", {"score": 5, "comment": "Quick."})

    note("The numbers the desk runs on, which a client is not shown at all.")
    api.get("/api/v1/support/stats", token=desk)

    asyncio.run(_support_socket(api, desk, bruno, dara))

    _support_surface_covered(api)


def _support_surface_covered(api: Api) -> None:
    """Assert the section above left no route and no command untoured.

    Asked of the app's own registries rather than of a list kept here, for the
    reason the admin section walks Django's: a second list is a list that goes
    stale quietly, and a tour that has stopped exercising an endpoint is a tour
    describing an app it no longer checks. Adding a route or a command without
    showing it here fails the tour, which is the point.
    """
    import re

    from apps.support.rest import router
    from apps.support.sockets import SupportSocket

    missed_routes: list[str] = []
    total_routes = 0
    for path, view in router.path_operations.items():
        # "/{ticket_id}/messages" is a pattern, not a path: the tour visited it
        # with a real id in place, so each placeholder matches one segment.
        literals = re.split(r"\{[^}]+\}", f"/api/v1/support{path}")
        pattern = re.compile("^" + "[^/]+".join(re.escape(part) for part in literals) + "$")
        for operation in view.operations:
            for method in operation.methods:
                total_routes += 1
                if not any(
                    seen_method == method and pattern.match(seen_path)
                    for seen_method, seen_path in api.visited
                ):
                    missed_routes.append(f"{method} /api/v1/support{path}")

    commands = set(SupportSocket.commands())
    missed_commands = sorted(commands - Socket.sent)

    if missed_routes or missed_commands:
        raise WalkthroughError(
            "the support tour skipped "
            + ", ".join(sorted(missed_routes) + [f"socket:{name}" for name in missed_commands])
        )
    print(
        f"  {DIM}│ toured {total_routes} of {total_routes} support endpoints and "
        f"{len(commands)} of {len(commands)} socket commands{OFF}"
    )


def desk_token(user: Any) -> str:
    """A real credential for somebody the tour did not sign in as."""
    from django.test import RequestFactory

    from infrastructure.auth.core.sessions import issue_credentials

    issued = issue_credentials(RequestFactory().post("/"), user, method="password")
    return str(issued.access_token)


async def _support_socket(api: Api, desk: str, bruno: Any, dara: Any) -> None:
    """Two connections, because this socket only makes sense as a conversation."""
    from django.conf import settings

    path = settings.SUPPORT_WS_PATH

    note(
        "This socket is useless before it is authenticated, and that is the "
        "design: unlike the notification one it has no public traffic to "
        "deliver. Every frame belongs to a named conversation."
    )
    async with Socket(path) as anonymous:
        ready = await anonymous.open()
        if ready["authenticated"]:
            raise WalkthroughError("the support socket accepted a connection as somebody")
        await anonymous.command({"command": "tickets"}, "error")

        note(
            "Naming yourself in the handshake is one way in; this is the "
            "other, for a page that opened the socket before the sign-in "
            "finished. `whoami` is how a client that reconnected asks which "
            "of the two it turned out to be."
        )
        await anonymous.command({"command": "whoami"}, "whoami", show=False)
        await anonymous.command(
            {"command": "authenticate", "token": api.token}, "authenticated", show=False
        )
        signed_in = await anonymous.command({"command": "whoami"}, "whoami", show=False)
        if not signed_in["authenticated"]:
            raise WalkthroughError("the socket stayed anonymous after authenticating")

        note(
            "And back out again without dropping the connection, which is what "
            "a shared browser signing out needs: the account is forgotten "
            "immediately, and the credential itself stays the auth app's to "
            "revoke."
        )
        await anonymous.command({"command": "deauthenticate"}, "deauthenticated", show=False)
        await anonymous.command({"command": "tickets"}, "error", show=False)

    note(
        "Two connections now, one each side of the desk, both authenticated in "
        "the handshake so neither waits a round trip."
    )
    async with (
        Socket(path, query=f"token={api.token}", label="client") as client,
        Socket(path, query=f"token={desk}", label="desk") as agent,
    ):
        await client.open()
        await agent.open()

        note(
            "A chat needs no subject and no category. Opening it over the "
            "socket subscribes this connection in the same round trip -- a "
            "client that had to subscribe afterwards would miss whatever the "
            "desk said in between."
        )
        opened = await client.command(
            {"command": "open", "kind": "chat", "body": "Is the refund through yet?"},
            "opened",
            show=False,
            patient=True,
        )
        chat = opened["ticket"]["id"]
        print(f"  {DIM}│ {opened['ticket']['reference']}{OFF}")

        note("The desk joins the thread and is handed its tail, so it has something to render.")
        await agent.command(
            {"command": "subscribe", "ticket": chat}, "subscribed", show=False, patient=True
        )

        note(
            "The reference data a client needs to render a composer, fetched "
            "over the socket rather than over HTTP beside it."
        )
        await client.command({"command": "categories"}, "categories", show=False, patient=True)
        await agent.command({"command": "tags"}, "tags", show=False, patient=True)
        await agent.command({"command": "canned"}, "canned", show=False, patient=True)

        note(
            "The desk's whole queue-working vocabulary is here too: take it, "
            "hand it on, reprioritise, tag, and bring somebody else in."
        )
        await agent.command(
            {"command": "claim", "ticket": chat}, "assigned", show=False, patient=True
        )
        await agent.command(
            {"command": "assign", "ticket": chat, "agent": str(bruno.pk)},
            "assigned",
            show=False,
            patient=True,
        )
        await agent.command(
            {"command": "priority", "ticket": chat, "priority": "urgent"},
            "priority",
            show=False,
            patient=True,
        )
        await agent.command(
            {"command": "tag", "ticket": chat, "tags": ["escalated"]},
            "tagged",
            show=False,
            patient=True,
        )
        await agent.command(
            {"command": "invite", "ticket": chat, "account": str(dara.pk), "role": "observer"},
            "invited",
            show=False,
            patient=True,
        )

        note(
            "Presence is the socket's own: who is actually looking at the "
            "thread right now, which no endpoint can answer because HTTP has "
            "nobody to stop asking."
        )
        await client.command(
            {"command": "presence", "ticket": chat, "present": True},
            "presence_ack",
            show=False,
            patient=True,
        )

        note("A typing indicator: not stored, and worth nothing unless it is live.")
        await agent.command(
            {"command": "typing", "ticket": chat, "typing": True},
            "typing_ack",
            show=False,
            patient=True,
        )
        await client.frame("typing", show=False, patient=True)

        note("The desk answers, and the client hears it without asking for anything.")
        await agent.command(
            {"command": "send", "ticket": chat, "body": "It went out this morning."},
            "sent",
            show=False,
            patient=True,
        )
        await client.frame("message", patient=True)

        note(
            "An internal note is published to the same thread and dropped on "
            "the way out for a connection that may not read it. This is the "
            "app's one real confidentiality rule, and this is where it is "
            "enforced for everybody who is connected."
        )
        await agent.command(
            {"command": "note", "ticket": chat, "body": "Refund reference RF-8812."},
            "sent",
            show=False,
            patient=True,
        )
        await agent.command(
            {"command": "send", "ticket": chat, "body": "Anything else?"},
            "sent",
            show=False,
            patient=True,
        )

        note(
            "The next thing the client is sent is the public message. The note "
            "between them never reached this connection at all."
        )
        heard = await client.frame("message", show=False, patient=True)
        print(f"  {DIM}│ {heard['message']['body']}{OFF}")
        if "RF-8812" in heard["message"]["body"]:
            raise WalkthroughError("the client was sent the desk's internal note")

        note(
            "The client says something of its own, then rewrites it and takes "
            "it back -- the same two rules the endpoints enforce, applied by "
            "the same service underneath."
        )
        mine = await client.command(
            {"command": "send", "ticket": chat, "body": "No, that is everythng."},
            "sent",
            show=False,
            patient=True,
        )
        await client.command(
            {
                "command": "edit",
                "message": mine["message"]["id"],
                "body": "No, that is everything.",
            },
            "edited",
            show=False,
            patient=True,
        )
        await client.command(
            {"command": "delete", "message": mine["message"]["id"]},
            "deleted",
            show=False,
            patient=True,
        )

        note(
            "Reading is the socket's too: a page of the thread, the badge, and "
            "putting a whole thread back into it -- so a client that opened "
            "this connection never has to reach for HTTP to render itself."
        )
        await client.command(
            {"command": "messages", "ticket": chat, "limit": 5},
            "messages",
            show=False,
            patient=True,
        )
        await client.command({"command": "unread"}, "unread", show=False, patient=True)
        await client.command(
            {"command": "unread_ticket", "ticket": chat},
            "unread_ticket",
            show=False,
            patient=True,
        )

        note("The socket does everything the endpoints do, so a client needs no HTTP beside it.")
        await client.command({"command": "read", "ticket": chat}, "read", show=False, patient=True)
        await agent.command(
            {"command": "status", "ticket": chat, "status": "pending"},
            "status",
            show=False,
            patient=True,
        )
        await client.command(
            {"command": "close", "ticket": chat}, "status", show=False, patient=True
        )
        await client.command(
            {"command": "reopen", "ticket": chat}, "status", show=False, patient=True
        )
        await client.command(
            {"command": "close", "ticket": chat}, "status", show=False, patient=True
        )
        await client.command(
            {"command": "rate", "ticket": chat, "score": 5}, "rated", show=False, patient=True
        )
        await agent.command({"command": "stats"}, "stats", patient=True)

        note(
            "Leaving a thread stops it being sent without dropping the "
            "connection. The channel stays joined underneath -- a subscription "
            "can be added to but not removed from -- and the filter is what "
            "goes quiet."
        )
        await agent.command(
            {"command": "unsubscribe", "ticket": chat}, "unsubscribed", show=False, patient=True
        )

        note(
            "A refusal is a frame, not a close -- a mistyped id should cost one "
            "message, not the conversation flowing over the connection."
        )
        await client.command(
            {"command": "ticket", "ticket": "not-a-uuid"}, "error", show=False, patient=True
        )

        note("-- and the proof is that the connection is still answering.")
        await client.command({"command": "ping"}, "pong", show=False, patient=True)


def section_email_code(api: Api) -> None:
    heading(
        11,
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
        12,
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
        13,
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
        14,
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
        15,
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
        16,
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
        17,
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
        18,
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


class AdminTour:
    """A signed-in staff browser: one page, one printed line.

    Separate from :class:`Api` because it is a different kind of client. The API
    carries a bearer token and reads JSON; the admin carries a session cookie
    and reads HTML, and the thing worth printing about an admin page is not its
    body but whether it rendered at all.
    """

    def __init__(self) -> None:
        from django.contrib.auth import get_user_model
        from django.test import Client

        self.client = Client()
        self.user = get_user_model().objects.create_superuser(
            username="root", email="root@example.com", password=PASSWORD
        )
        self.client.force_login(self.user)
        self.visited = 0

    def visit(self, path: str, label: str = "", *, expect: int = 200) -> Any:
        response = self.client.get(path)
        ok = response.status_code == expect
        tint = GREEN if ok else RED
        suffix = f"  {DIM}{label}{OFF}" if label else ""
        print(f"  {CYAN}{'GET':<6}{OFF} {path} {tint}→ {response.status_code}{OFF}{suffix}")
        if not ok:
            raise WalkthroughError(f"admin {path} returned {response.status_code}, not {expect}")
        self.visited += 1
        return response


def section_admin(api: Api) -> None:
    """Every registered admin, opened. The half of this project nobody curls."""
    from django.contrib import admin as django_admin
    from django.urls import reverse

    heading(
        19,
        "The admin, every app of it",
        "all apps",
        "The API is half the project; the other half is the screen the people "
        "who run it use. Every model any installed app registered is opened "
        "here, so an app that ships a broken changelist fails the tour.",
    )

    tour = AdminTour()
    note("The index lists exactly the apps this .env turned on.")
    tour.visit("/admin/", "the index")

    # Walked from the registry rather than from a list written here, so an app
    # added tomorrow is covered without this file being edited -- and an app
    # left out of the .env is simply absent rather than a hard-coded 404.
    registered = sorted(
        django_admin.site._registry.items(),
        key=lambda row: (row[0]._meta.app_label, row[0]._meta.object_name or ""),
    )
    grouped: dict[str, list[Any]] = {}
    for model, _ in registered:
        grouped.setdefault(model._meta.app_label, []).append(model)

    for app_label, models in grouped.items():
        note(f"{app_label}: {len(models)} model(s) registered.")
        for model in models:
            name = model._meta.model_name
            columns = getattr(django_admin.site._registry[model], "list_display", ())
            tour.visit(
                reverse(f"admin:{app_label}_{name}_changelist"),
                f"{model._meta.verbose_name_plural} · {len(columns)} columns",
            )

    note(
        "The changelists are only the door. Two screens are worth opening on "
        "their own, because neither is an ordinary Django change form."
    )
    _admin_content_screen(tour)
    _admin_notification_form(tour)
    _admin_shop_order(tour)

    note(f"{tour.visited} admin pages opened, all of them rendering.")


def _admin_content_screen(tour: AdminTour) -> None:
    """The CMS's own editing screen: one page, one language, a widget per type."""
    from django.apps import apps as django_apps
    from django.urls import reverse

    if not django_apps.is_installed("apps.cms"):
        return

    from apps.cms.models import Page

    page = Page.objects.filter(slug="home").first()
    if page is None:  # pragma: no cover - only if the CMS section did not run
        return
    note(
        "The CMS content screen. Not a change form: sections in reader order, "
        "one language at a time, and the input each field type deserves."
    )
    url = reverse("admin:cms_page_content", args=(page.pk,))
    body = tour.visit(url, "page content, default language").content.decode()
    tour.visit(f"{url}?language=fa", "the same page in Persian")
    for widget, what in (
        ('type="file"', "an upload button on the image field"),
        ('type="url"', "the address box beside it"),
        ("<textarea", "a box with rows for the long types"),
        ('type="color"', "a colour picker"),
        ("<select", "a dropdown over the choice field's own options"),
        ("multiple", "a multi-file input for the gallery"),
    ):
        found = GREEN + "found" + OFF if widget in body else RED + "missing" + OFF
        print(f"  {DIM}│{OFF} {widget:<16} {found}  {DIM}{what}{OFF}")
    tour.visit(reverse("admin:cms_sitesettings_changelist"), "site metadata, per language")


def _admin_notification_form(tour: AdminTour) -> None:
    """Writing one is an admin job, so the add form is part of the app."""
    from django.apps import apps as django_apps
    from django.urls import reverse

    if not django_apps.is_installed("apps.notifications"):
        return
    note("Writing a notification to everybody is a form, not a shell session.")
    tour.visit(reverse("admin:notifications_notification_add"), "compose")


def _admin_shop_order(tour: AdminTour) -> None:
    """An order and its invoice, which is where a shop is actually run from."""
    from django.apps import apps as django_apps
    from django.urls import reverse

    if not django_apps.is_installed("apps.shop"):
        return

    from apps.shop.models import Order

    order = Order.objects.first()
    if order is None:  # pragma: no cover - only if the shop section did not run
        return
    note("The order placed above, as whoever packs it sees it.")
    tour.visit(reverse("admin:shop_order_change", args=(order.pk,)), f"order {order.number}")
    tour.visit(reverse("admin:shop_product_add"), "add a product")


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
        section_shop(api)
        section_support(api)
        section_email_code(api)
        section_sms_code(api)
        section_magic_link(api)
        section_twofactor(api)
        section_tokens(api)
        section_social(api)
        section_audit(api)
        section_openapi(api)
        section_admin(api)
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
