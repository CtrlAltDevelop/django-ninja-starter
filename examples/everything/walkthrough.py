#!/usr/bin/env python3
"""Exercise every app this starter ships, in a single run.

    python examples/everything/walkthrough.py

Four login methods, four second factors, four social providers, a token mode,
the account and health endpoints, the audit trail and the generated OpenAPI
document -- printed as a transcript of the calls a real client would make.

Two choices make that possible with no external service and no setup:

*   It drives Django's test client in-process instead of a running server. The
    codes these flows send out are the whole point of the demo, and only a
    process holding the outbox can read them back. Every call still goes through
    URL routing, middleware, authentication and the view -- the same stack
    ``runserver`` uses, and the paths printed below are the real paths. The
    README has the curl equivalents for a live server.
*   It turns every app on through environment variables it sets itself, against
    a throwaway in-memory database. Nothing here reads or writes your .env, and
    nothing lands in db.sqlite3.

Anything already set in the shell wins, so one switch re-runs the whole tour
under a different token mode:

    DJANGO_AUTH_TOKEN_MODE=sliding python examples/everything/walkthrough.py
"""

import io
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

PASSWORD = "corr3ct-horse-battery-staple"
NEW_PASSWORD = "an0ther-horse-battery-staple"

# Every app, on. These are the same switches as env.example next door; the
# values marked "demo only" are the ones a real deployment must replace.
ENVIRONMENT = {
    "DJANGO_SETTINGS_MODULE": "config.settings.development",
    "DJANGO_SECRET_KEY": "walkthrough-only-secret-key",  # demo only
    "DJANGO_DB_NAME": ":memory:",  # demo only: a database that never hits disk
    # Login methods, second factors, social providers, token mode.
    "DJANGO_AUTH_METHODS": "password,email_code,sms_code,magic_link",
    "DJANGO_AUTH_SECOND_FACTORS": "totp,sms,email,recovery",
    "DJANGO_OAUTH_MODE": "all",
    "DJANGO_OAUTH_PROVIDERS": "google,apple,microsoft,github",
    "DJANGO_AUTH_TOKEN_MODE": "rotation",
    # Codes and links have to come back to this process rather than go out to a
    # carrier, and the challenge store has to work without a Redis to talk to.
    "DJANGO_AUTH_CHALLENGE_STORE": "infrastructure.auth.core.challenges.LocMemChallengeStore",
    "DJANGO_AUTH_SMS_BACKEND": "infrastructure.auth.core.delivery.LocMemSmsBackend",
    "DJANGO_AUTH_EMAIL_BACKEND": "infrastructure.auth.core.delivery.LocMemEmailBackend",
    "DJANGO_AUTH_RESEND_COOLDOWN_SECONDS": "0",  # demo only: no waiting between sends
    # Settings the enabled apps declare as requirements. Leaving any of them out
    # is what `manage.py check` reports, which section 2 demonstrates.
    "DJANGO_AUTH_JWT_SIGNING_KEY": "walkthrough-only-jwt-signing-key",  # demo only
    "DJANGO_AUTH_EMAIL_FROM": "sign-in@example.test",
    "DJANGO_AUTH_SMS_FROM": "+15555550100",
    "DJANGO_AUTH_MAGIC_LINK_BASE_URL": "https://example.test/auth/link",
    "DJANGO_AUTH_PASSWORD_RESET_BASE_URL": "https://example.test/auth/reset",
    # Social credentials. Real ones come from each provider's console; these are
    # enough to build an authorization URL, which is as far as an offline demo
    # can go -- the callback is answered by the provider, not by us.
    "GOOGLE_OAUTH_CLIENT_ID": "demo-google-client",
    "GOOGLE_OAUTH_CLIENT_SECRET": "demo-google-secret",
    "GOOGLE_OAUTH_REDIRECT_URI": "https://example.test/api/v1/oauth/google/callback",
    "APPLE_OAUTH_CLIENT_ID": "test.apple.service",
    "APPLE_OAUTH_TEAM_ID": "DEMOTEAM01",
    "APPLE_OAUTH_KEY_ID": "DEMOKEY001",
    "APPLE_OAUTH_PRIVATE_KEY": "demo-apple-private-key",
    "APPLE_OAUTH_REDIRECT_URI": "https://example.test/api/v1/oauth/apple/callback",
    "MICROSOFT_OAUTH_CLIENT_ID": "demo-microsoft-client",
    "MICROSOFT_OAUTH_CLIENT_SECRET": "demo-microsoft-secret",
    "MICROSOFT_OAUTH_TENANT": "common",
    "MICROSOFT_OAUTH_REDIRECT_URI": "https://example.test/api/v1/oauth/microsoft/callback",
    "GITHUB_OAUTH_CLIENT_ID": "demo-github-client",
    "GITHUB_OAUTH_CLIENT_SECRET": "demo-github-secret",
    "GITHUB_OAUTH_REDIRECT_URI": "https://example.test/api/v1/oauth/github/callback",
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
    ) -> Any:
        bearer = self.token if token is None else token
        extra = {"HTTP_AUTHORIZATION": f"Bearer {bearer}"} if bearer else {}
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


def configure() -> None:
    """Turn everything on, then start Django against a throwaway database."""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    for key, value in ENVIRONMENT.items():
        os.environ.setdefault(key, value)

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
        "The one warning is deliberate: this tour uses the in-process challenge "
        "store so it needs no Redis, and that store says so about itself."
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


def section_email_code(api: Api) -> None:
    heading(
        6,
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
        7,
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
        8,
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
        9,
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
        10,
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
        11,
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
        12,
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
        13,
        "The document all of that produced",
        "config.api",
        "One NinjaAPI per version, every enabled app's router attached to it. "
        "Swagger is at /api/docs with a selector for the versions.",
    )
    schema = api.get("/api/v1/openapi.json", token="", show=False)
    by_tag: dict[str, int] = {}
    for operations in schema["paths"].values():
        for operation in operations.values():
            for tag in operation.get("tags", ["untagged"]):
                by_tag[tag] = by_tag.get(tag, 0) + 1
    for tag, count in sorted(by_tag.items()):
        print(f"  {count:>3} operations  {tag}")
    total = sum(by_tag.values())
    print(f"\n  {BOLD}{total} operations across {len(schema['paths'])} paths{OFF}")
    schemes = ", ".join(schema["components"].get("securitySchemes", {}))
    print(f"  {DIM}security schemes: {schemes}{OFF}")


def main() -> int:
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


if __name__ == "__main__":
    raise SystemExit(main())
