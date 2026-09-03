import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

from infrastructure.common.registry import registered_app_configs

BASE_DIR = Path(__file__).resolve().parents[3]
# `DJANGO_ENV_FILE` names the file, so a deployment can keep several side by
# side, and -- set to an empty string -- so a process can ask for none at all.
# A test that builds its environment from nothing needs that second option:
# without it a developer's own `.env` would be read back in and the process
# would be configured by whatever happens to be on this machine.
_ENV_FILE = os.environ.get("DJANGO_ENV_FILE", ".env")
if _ENV_FILE:
    load_dotenv(BASE_DIR / _ENV_FILE)

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-development-key")
DEBUG = False
ALLOWED_HOSTS = [
    host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if host.strip()
]

# The account model everything else resolves to. Swapping it is supported --
# AUTH_USER_MODEL is what every table references -- but doing so means taking
# over the profile relation the login flows enrich.
ACCOUNTS_APP = "infrastructure.accounts.apps.AccountsConfig"
AUTH_USER_MODEL = os.getenv("DJANGO_AUTH_USER_MODEL", "accounts.User")
ACCOUNTS_DEFAULT_LOCALE = os.getenv("DJANGO_ACCOUNTS_DEFAULT_LOCALE", "en-us")
ACCOUNTS_DEFAULT_TIMEZONE = os.getenv("DJANGO_ACCOUNTS_DEFAULT_TIMEZONE", "UTC")
# Swagger groups its operations by tag and prints the description under the
# group heading, so both are declared here, beside the router they belong to. A
# router whose app is not installed contributes neither, which is what keeps the
# document's tag list to the apps this deployment actually publishes.
ACCOUNT_ROUTERS = [
    {
        "prefix": "/users",
        "router": "infrastructure.accounts.rest.api.router",
        "tag": "Users",
        "description": (
            "The account every login resolves to, and the profile the login "
            "flows enrich as they learn things."
        ),
    }
]

OAUTH_MODE = os.getenv("DJANGO_OAUTH_MODE", "none").lower()
OAUTH_MODE_APPS = {
    "none": [],
    "sliding": ["infrastructure.oauth.sliding.apps.OAuthSlidingConfig"],
    "session": ["infrastructure.oauth.session.apps.OAuthSessionConfig"],
    "rotation": ["infrastructure.oauth.rotation.apps.OAuthRotationConfig"],
    "all": [
        "infrastructure.oauth.sliding.apps.OAuthSlidingConfig",
        "infrastructure.oauth.session.apps.OAuthSessionConfig",
        "infrastructure.oauth.rotation.apps.OAuthRotationConfig",
    ],
}
if OAUTH_MODE not in OAUTH_MODE_APPS:
    raise ImproperlyConfigured(
        "DJANGO_OAUTH_MODE must be one of: none, sliding, session, rotation, all"
    )
OAUTH_PROVIDER_APPS = {
    "google": "infrastructure.oauth.google.apps.OAuthGoogleConfig",
    "apple": "infrastructure.oauth.apple.apps.OAuthAppleConfig",
    "microsoft": "infrastructure.oauth.microsoft.apps.OAuthMicrosoftConfig",
    "github": "infrastructure.oauth.github.apps.OAuthGitHubConfig",
}
OAUTH_PROVIDERS = list(
    dict.fromkeys(
        provider.strip().lower()
        for provider in os.getenv("DJANGO_OAUTH_PROVIDERS", "").split(",")
        if provider.strip()
    )
)
unknown_oauth_providers = set(OAUTH_PROVIDERS) - OAUTH_PROVIDER_APPS.keys()
if unknown_oauth_providers:
    raise ImproperlyConfigured(
        f"Unknown DJANGO_OAUTH_PROVIDERS: {', '.join(sorted(unknown_oauth_providers))}"
    )
OAUTH_CORE_APP = "infrastructure.oauth.core.apps.OAuthCoreConfig"
OAUTH_INSTALLED_APPS = []
if OAUTH_MODE != "none" or OAUTH_PROVIDERS:
    OAUTH_INSTALLED_APPS.append(OAUTH_CORE_APP)
OAUTH_INSTALLED_APPS.extend(OAUTH_MODE_APPS[OAUTH_MODE])
OAUTH_INSTALLED_APPS.extend(OAUTH_PROVIDER_APPS[provider] for provider in OAUTH_PROVIDERS)

# Written out rather than title-cased from the provider name, because `.title()`
# spells GitHub "Github" -- and the tag is the group heading a reader sees, next
# to an admin that spells it correctly.
OAUTH_PROVIDER_TAGS = {
    "google": {
        "tag": "OAuth - Google",
        "description": "Sign in with Google. OIDC, so the profile comes out of the ID token.",
    },
    "apple": {
        "tag": "OAuth - Apple",
        "description": (
            "Sign in with Apple. The callback is a cross-site form POST, and the "
            "name arrives once, on the first sign-in only."
        ),
    },
    "microsoft": {
        "tag": "OAuth - Microsoft",
        "description": (
            "Sign in with Microsoft. The tenant this deployment names decides who "
            "may sign in at all."
        ),
    },
    "github": {
        "tag": "OAuth - GitHub",
        "description": (
            "Sign in with GitHub. Not OIDC, so the profile is fetched over REST "
            "after the token is exchanged."
        ),
    },
}
OAUTH_PROVIDER_ROUTERS = [
    {
        "prefix": f"/oauth/{provider}",
        "router": f"infrastructure.oauth.{provider}.rest.router",
        **OAUTH_PROVIDER_TAGS[provider],
    }
    for provider in OAUTH_PROVIDERS
]
OAUTH_PROVIDER_CONFIG = {
    "google": {
        "client_id": os.getenv("GOOGLE_OAUTH_CLIENT_ID", ""),
        "client_secret": os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", ""),
        "redirect_uri": os.getenv("GOOGLE_OAUTH_REDIRECT_URI", ""),
        "scopes": os.getenv("GOOGLE_OAUTH_SCOPES", "openid email profile").split(),
    },
    "apple": {
        "client_id": os.getenv("APPLE_OAUTH_CLIENT_ID", ""),
        "team_id": os.getenv("APPLE_OAUTH_TEAM_ID", ""),
        "key_id": os.getenv("APPLE_OAUTH_KEY_ID", ""),
        "private_key": os.getenv("APPLE_OAUTH_PRIVATE_KEY", ""),
        "redirect_uri": os.getenv("APPLE_OAUTH_REDIRECT_URI", ""),
        "scopes": os.getenv("APPLE_OAUTH_SCOPES", "name email").split(),
    },
    "microsoft": {
        "client_id": os.getenv("MICROSOFT_OAUTH_CLIENT_ID", ""),
        "client_secret": os.getenv("MICROSOFT_OAUTH_CLIENT_SECRET", ""),
        "redirect_uri": os.getenv("MICROSOFT_OAUTH_REDIRECT_URI", ""),
        "tenant": os.getenv("MICROSOFT_OAUTH_TENANT", "common"),
        "scopes": os.getenv(
            "MICROSOFT_OAUTH_SCOPES", "openid email profile offline_access"
        ).split(),
    },
    "github": {
        "client_id": os.getenv("GITHUB_OAUTH_CLIENT_ID", ""),
        "client_secret": os.getenv("GITHUB_OAUTH_CLIENT_SECRET", ""),
        "redirect_uri": os.getenv("GITHUB_OAUTH_REDIRECT_URI", ""),
        "scopes": os.getenv("GITHUB_OAUTH_SCOPES", "read:user user:email").split(),
    },
}
AUTH_METHOD_APPS = {
    "password": "infrastructure.auth.password.apps.AuthPasswordConfig",
    "email_code": "infrastructure.auth.email_code.apps.AuthEmailCodeConfig",
    "sms_code": "infrastructure.auth.sms_code.apps.AuthSmsCodeConfig",
    "magic_link": "infrastructure.auth.magic_link.apps.AuthMagicLinkConfig",
}
AUTH_METHODS = list(
    dict.fromkeys(
        method.strip().lower()
        for method in os.getenv("DJANGO_AUTH_METHODS", "").split(",")
        if method.strip()
    )
)
unknown_auth_methods = set(AUTH_METHODS) - AUTH_METHOD_APPS.keys()
if unknown_auth_methods:
    raise ImproperlyConfigured(
        f"Unknown DJANGO_AUTH_METHODS: {', '.join(sorted(unknown_auth_methods))}"
    )
AUTH_SECOND_FACTORS = list(
    dict.fromkeys(
        factor.strip().lower()
        for factor in os.getenv("DJANGO_AUTH_SECOND_FACTORS", "").split(",")
        if factor.strip()
    )
)
SUPPORTED_SECOND_FACTORS = {"totp", "sms", "email", "recovery"}
unknown_second_factors = set(AUTH_SECOND_FACTORS) - SUPPORTED_SECOND_FACTORS
if unknown_second_factors:
    raise ImproperlyConfigured(
        f"Unknown DJANGO_AUTH_SECOND_FACTORS: {', '.join(sorted(unknown_second_factors))}"
    )
if AUTH_SECOND_FACTORS and not AUTH_METHODS:
    raise ImproperlyConfigured(
        "DJANGO_AUTH_SECOND_FACTORS requires at least one DJANGO_AUTH_METHODS entry"
    )
AUTH_INSTALLED_APPS = []
if AUTH_METHODS or AUTH_SECOND_FACTORS:
    AUTH_INSTALLED_APPS.append("infrastructure.auth.core.apps.AuthCoreConfig")
AUTH_INSTALLED_APPS.extend(AUTH_METHOD_APPS[method] for method in AUTH_METHODS)
if AUTH_SECOND_FACTORS:
    AUTH_INSTALLED_APPS.append("infrastructure.auth.twofactor.apps.AuthTwoFactorConfig")

# As with the providers above: spelled out, because title-casing `sms_code`
# produces "Sms Code" and the app's own verbose name does not.
AUTH_METHOD_TAGS = {
    "password": {
        "tag": "Auth - Password",
        "description": (
            "Sign up and sign in with a password, change it, and recover a "
            "forgotten one. The identifier is the username or the email address."
        ),
    },
    "email_code": {
        "tag": "Auth - Email Code",
        "description": (
            "Sign up and sign in with a one-time code emailed to the address. "
            "Start returns a ticket; only the ticket and the code together are a login."
        ),
    },
    "sms_code": {
        "tag": "Auth - SMS Code",
        "description": (
            "Sign up and sign in with a one-time code sent by SMS. The one "
            "identifier that produces an account with no email address at all."
        ),
    },
    "magic_link": {
        "tag": "Auth - Magic Link",
        "description": (
            "Sign up and sign in by following an emailed link. Good exactly "
            "once: the token in the URL is the whole credential."
        ),
    },
}
AUTH_METHOD_ROUTERS = [
    {
        "prefix": f"/auth/{method.replace('_', '-')}",
        "router": f"infrastructure.auth.{method}.rest.router",
        **AUTH_METHOD_TAGS[method],
    }
    for method in AUTH_METHODS
]
if AUTH_SECOND_FACTORS:
    AUTH_METHOD_ROUTERS.append(
        {
            "prefix": "/auth/2fa",
            "router": "infrastructure.auth.twofactor.rest.router",
            "tag": "Auth - Two Factor",
            "description": (
                "Enrol, confirm and use the second factors this deployment "
                "allows. Enrolment is not real until a code confirms it, and a "
                "login with one enrolled finishes at /auth/2fa/verify."
            ),
        }
    )

AUTH_TOKEN_MODES = {"none", "sliding", "session", "rotation"}
AUTH_DEFAULT_TOKEN_MODE = "rotation"
_requested_token_mode = os.getenv("DJANGO_AUTH_TOKEN_MODE", "").lower()
if _requested_token_mode:
    AUTH_TOKEN_MODE = _requested_token_mode
elif OAUTH_MODE in {"sliding", "session", "rotation"}:
    AUTH_TOKEN_MODE = OAUTH_MODE
elif OAUTH_MODE == "all" or AUTH_METHODS or AUTH_SECOND_FACTORS or OAUTH_PROVIDERS:
    # Something here signs people in, so they need a credential an API client can
    # actually carry. Falling through to `none` would hand out a session cookie
    # and an empty access token, which is a working login and an unusable API.
    AUTH_TOKEN_MODE = AUTH_DEFAULT_TOKEN_MODE
else:
    AUTH_TOKEN_MODE = "none"
if AUTH_TOKEN_MODE not in AUTH_TOKEN_MODES:
    raise ImproperlyConfigured(
        "DJANGO_AUTH_TOKEN_MODE must be one of: none, sliding, session, rotation"
    )
# The active mode owns the credential tables, so its app has to be installed --
# whether it was named through DJANGO_OAUTH_MODE or arrived at by the default
# above. Without this, enabling only a login method would issue tokens into a
# table that does not exist.
if AUTH_TOKEN_MODE != "none":
    if OAUTH_CORE_APP not in OAUTH_INSTALLED_APPS:
        OAUTH_INSTALLED_APPS.insert(0, OAUTH_CORE_APP)
    for _token_app in OAUTH_MODE_APPS[AUTH_TOKEN_MODE]:
        if _token_app not in OAUTH_INSTALLED_APPS:
            OAUTH_INSTALLED_APPS.append(_token_app)
# Refresh, revoke and session management for whichever mode is active. Always at
# the same prefix, so a client does not have to know which mode it is talking to.
AUTH_TOKEN_ROUTERS = (
    []
    if AUTH_TOKEN_MODE == "none"
    else [
        {
            "prefix": "/auth/token",
            "router": f"infrastructure.oauth.{AUTH_TOKEN_MODE}.rest.router",
            # Named for the prefix rather than for the mode behind it, because a
            # client reads the same group whichever mode is active -- which is
            # the whole point of publishing them at one prefix.
            "tag": "Auth - Token",
            "description": (
                "Refresh, revoke and list credentials, and trade a browser "
                f"session for one. Answered here by the {AUTH_TOKEN_MODE} mode."
            ),
        }
    ]
)
# Whether an admin session may be traded for a bearer token, which is what lets
# the Swagger page authorise itself for somebody already signed into the admin.
# Staff-only wherever it is on; turning it off unpublishes the route entirely, so
# nothing advertises a bridge this deployment does not want.
AUTH_SESSION_TOKEN_FOR_STAFF = (
    os.getenv("DJANGO_AUTH_SESSION_TOKEN_FOR_STAFF", "true").lower() == "true"
)

AUTH_REDIS_URL = os.getenv("DJANGO_AUTH_REDIS_URL", "redis://127.0.0.1:6379/0")
AUTH_CHALLENGE_STORE = os.getenv(
    "DJANGO_AUTH_CHALLENGE_STORE",
    "infrastructure.auth.core.challenges.RedisChallengeStore",
)
AUTH_CHALLENGE_TTL_SECONDS = int(os.getenv("DJANGO_AUTH_CHALLENGE_TTL_SECONDS", "300"))
AUTH_CHALLENGE_MAX_ATTEMPTS = int(os.getenv("DJANGO_AUTH_CHALLENGE_MAX_ATTEMPTS", "5"))
AUTH_PENDING_LOGIN_TTL_SECONDS = int(os.getenv("DJANGO_AUTH_PENDING_LOGIN_TTL_SECONDS", "600"))
AUTH_CODE_DIGITS = int(os.getenv("DJANGO_AUTH_CODE_DIGITS", "6"))
AUTH_RESEND_COOLDOWN_SECONDS = int(os.getenv("DJANGO_AUTH_RESEND_COOLDOWN_SECONDS", "30"))
AUTH_MAX_SENDS_PER_HOUR = int(os.getenv("DJANGO_AUTH_MAX_SENDS_PER_HOUR", "10"))
AUTH_ACCESS_TOKEN_TTL_SECONDS = int(os.getenv("DJANGO_AUTH_ACCESS_TOKEN_TTL_SECONDS", "3600"))
AUTH_REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("DJANGO_AUTH_REFRESH_TOKEN_TTL_SECONDS", "1209600"))
AUTH_SLIDING_IDLE_TIMEOUT_SECONDS = int(
    os.getenv("DJANGO_AUTH_SLIDING_IDLE_TIMEOUT_SECONDS", "900")
)
AUTH_SMS_BACKEND = os.getenv(
    "DJANGO_AUTH_SMS_BACKEND", "infrastructure.auth.core.delivery.ConsoleSmsBackend"
)
AUTH_EMAIL_BACKEND = os.getenv(
    "DJANGO_AUTH_EMAIL_BACKEND", "infrastructure.auth.core.delivery.DjangoEmailBackend"
)
AUTH_EMAIL_FROM = os.getenv("DJANGO_AUTH_EMAIL_FROM", "no-reply@example.com")
AUTH_SMS_FROM = os.getenv("DJANGO_AUTH_SMS_FROM", "")
AUTH_MAGIC_LINK_BASE_URL = os.getenv("DJANGO_AUTH_MAGIC_LINK_BASE_URL", "")
AUTH_PASSWORD_RESET_BASE_URL = os.getenv("DJANGO_AUTH_PASSWORD_RESET_BASE_URL", "")
AUTH_AUTO_CREATE_USERS = os.getenv("DJANGO_AUTH_AUTO_CREATE_USERS", "true").lower() == "true"
AUTH_TOTP_ISSUER = os.getenv("DJANGO_AUTH_TOTP_ISSUER", "Django Ninja Starter")
AUTH_RECOVERY_CODE_COUNT = int(os.getenv("DJANGO_AUTH_RECOVERY_CODE_COUNT", "10"))

# Signed credentials. HS256 needs only DJANGO_SECRET_KEY to work out of the box;
# an RS*/ES* algorithm needs a real key pair, which the system checks insist on
# rather than silently falling back to something weaker.
AUTH_JWT_ALGORITHM = os.getenv("DJANGO_AUTH_JWT_ALGORITHM", "HS256").upper()
AUTH_JWT_SIGNING_KEY = os.getenv("DJANGO_AUTH_JWT_SIGNING_KEY", "")
AUTH_JWT_VERIFYING_KEY = os.getenv("DJANGO_AUTH_JWT_VERIFYING_KEY", "")
AUTH_JWT_ISSUER = os.getenv("DJANGO_AUTH_JWT_ISSUER", "django-ninja-starter")
AUTH_JWT_AUDIENCE = os.getenv("DJANGO_AUTH_JWT_AUDIENCE", "")
AUTH_JWT_LEEWAY_SECONDS = int(os.getenv("DJANGO_AUTH_JWT_LEEWAY_SECONDS", "30"))

# The content app. Optional in the same way every login method is: naming it is
# what installs it, and a project that does not name it carries no CMS tables,
# publishes no CMS routes and never imports the package.
CMS_ENABLED = os.getenv("DJANGO_CMS_ENABLED", "false").lower() == "true"
CMS_APP = "apps.cms.apps.CmsConfig"
CMS_INSTALLED_APPS = [CMS_APP] if CMS_ENABLED else []
CMS_ROUTERS = (
    [
        {
            "prefix": "/cms",
            "router": "apps.cms.rest.router",
            "tag": "CMS",
            "description": (
                "Read the site an editor built: published pages, their sections "
                "and typed fields, menus, and the site metadata. Public, and "
                "translated per field with a fallback."
            ),
        }
    ]
    if CMS_ENABLED
    else []
)
# The languages content may be written in. Deliberately not Django's LANGUAGES,
# which lists every language it ships a name for: "the languages this content is
# written in" has to mean the handful an editor is really expected to fill in.
# Left empty, the app falls back to LANGUAGE_CODE on its own.
CMS_LANGUAGES = [
    code.strip().lower()
    for code in os.getenv("DJANGO_CMS_LANGUAGES", "").split(",")
    if code.strip()
]
CMS_PREVIEW_TTL_SECONDS = int(os.getenv("DJANGO_CMS_PREVIEW_TTL_SECONDS", str(60 * 60 * 24)))

# The notification app. Optional the same way the CMS is: naming it installs its
# tables, its routes and its socket, and a project that does not name it never
# imports the package.
NOTIFICATIONS_ENABLED = os.getenv("DJANGO_NOTIFICATIONS_ENABLED", "false").lower() == "true"
NOTIFICATIONS_APP = "apps.notifications.apps.NotificationsConfig"
NOTIFICATIONS_INSTALLED_APPS = [NOTIFICATIONS_APP] if NOTIFICATIONS_ENABLED else []
# Where the WebSocket is mounted. A setting rather than a constant because it is
# the one part of this app a reverse proxy has to be told about, and a proxy is
# usually easier to point at the app than the other way round. Declared above the
# router because the tag below quotes it.
NOTIFICATIONS_WS_PATH = os.getenv("DJANGO_NOTIFICATIONS_WS_PATH", "/ws/notifications")
# The socket, written into the tag rather than into a route. OpenAPI describes
# request/response over HTTP and has no vocabulary for a long-lived duplex
# connection, and Swagger has no transport to open one -- so a path published for
# it would render an operation whose "Try it out" cannot work. This is the half
# that can be told truthfully: the same group heading a reader is already looking
# at, in Markdown, saying what the socket is and what it accepts. AsyncAPI is the
# format that describes the rest.
NOTIFICATIONS_SOCKET_DOCS = f"""

### The live feed: `{NOTIFICATIONS_WS_PATH}`

A WebSocket, so it is not an operation on this page. `runserver` is WSGI and will
not serve it; any ASGI server will.

**It is useful before it is authenticated.** Connect with no credential at all
and you receive what was addressed to everybody. `{{"command": "authenticate",
"token": "..."}}` adds your own channel to the same connection and replays your
unread backlog, so a client opens one socket rather than one per audience. A
credential offered in the handshake -- `?token=`, a `bearer` subprotocol, an
`Authorization` header, a session cookie -- is honoured at connect instead, and
any command may carry the same `token` to sign in before it runs.

Every frame is JSON, with a `command` going up and a `type` coming down. **The
socket does everything the endpoints below do**, so a client holding one open
needs no HTTP client beside it.

Open to anyone: `ping`, `authenticate`, `whoami`, `deauthenticate`. Needing an
account: `list`, `get`, `count`, `unread`, `read`, `unread_one`, `read_all`,
`dismiss`, `restore`, `dismiss_all` -- each answered with a frame of the same
name, plus `ready`, `authenticated`, `deauthenticated`, `notification`, `state`,
`pong` and `error`. **Errors are frames, not closes** -- a mistyped id costs one
message, not the connection -- and their `title` is the same vocabulary the
endpoints below answer with.

The app's own `docs/notifications.md` carries the frame-by-frame reference.
"""
NOTIFICATIONS_ROUTERS = (
    [
        {
            "prefix": "/notifications",
            "router": "apps.notifications.rest.router",
            "tag": "Notifications",
            "description": (
                "The history a client reads when it opens, and the read state it "
                "keeps. Nothing here creates a notification -- the code with "
                "something to say does -- and new ones arrive over the socket."
                + NOTIFICATIONS_SOCKET_DOCS
            ),
        }
    ]
    if NOTIFICATIONS_ENABLED
    else []
)
# How a notification created in one process reaches sockets held open by another.
# The default fans out inside a single process only, which is right for
# development and wrong for anything running more than one worker -- the app's
# settings contract says so out loud.
NOTIFICATIONS_BROKER = os.getenv(
    "DJANGO_NOTIFICATIONS_BROKER", "apps.notifications.broadcast.MemoryBroker"
)
NOTIFICATIONS_REDIS_URL = os.getenv("DJANGO_NOTIFICATIONS_REDIS_URL", AUTH_REDIS_URL)
NOTIFICATIONS_CHANNEL_PREFIX = os.getenv("DJANGO_NOTIFICATIONS_CHANNEL_PREFIX", "notifications")
# How many unread notifications a socket is caught up with on connect. The list
# endpoint is where the rest of the history lives; this is only so that a client
# that reconnects does not have to make an HTTP call to find out what it missed.
NOTIFICATIONS_SOCKET_BACKLOG = int(os.getenv("DJANGO_NOTIFICATIONS_SOCKET_BACKLOG", "20"))
# How long a notification is kept. `manage.py notifications_prune` deletes what
# is older, and nothing does so on its own: deleting rows on a timer nobody
# asked for is the kind of surprise a starter should not ship. Zero -- the
# default -- means keep everything, so a project that never schedules the
# command never silently loses history.
NOTIFICATIONS_RETENTION_DAYS = int(os.getenv("DJANGO_NOTIFICATIONS_RETENTION_DAYS", "0"))

# The shop app. Optional the same way the CMS and the notifications are: naming
# it installs its tables, its routes and its admin, and a project that does not
# name it never imports the package.
SHOP_ENABLED = os.getenv("DJANGO_SHOP_ENABLED", "false").lower() == "true"
SHOP_APP = "apps.shop.apps.ShopConfig"
SHOP_INSTALLED_APPS = [SHOP_APP] if SHOP_ENABLED else []
SHOP_ROUTERS = (
    [
        {
            "prefix": "/shop",
            "router": "apps.shop.rest.router",
            "tag": "Shop",
            "description": (
                "The catalogue a shop keeps: categories and the attributes they "
                "declare, products with their variants and specs, curated "
                "collections, and the discounts running on them. Reading is "
                "public; the basket, the reviews and the likes belong to the "
                "account that called. Nothing here writes to the catalogue -- "
                "that is the admin's job."
            ),
        }
    ]
    if SHOP_ENABLED
    else []
)
# The one currency every price is quoted in. A catalogue priced in several needs
# a conversion policy, a rounding policy and a display policy, and inventing
# those silently is worse than saying a shop has one currency.
SHOP_CURRENCY = os.getenv("DJANGO_SHOP_CURRENCY", "USD").upper()
# Whether a review waits for a moderator before anybody else can read it. On by
# default: a storefront that publishes whatever is typed into it is a spam
# target from the first week.
SHOP_REVIEW_MODERATION = os.getenv("DJANGO_SHOP_REVIEW_MODERATION", "true").lower() == "true"
SHOP_MAX_ITEM_QUANTITY = int(os.getenv("DJANGO_SHOP_MAX_ITEM_QUANTITY", "99"))
SHOP_PAGE_SIZE = int(os.getenv("DJANGO_SHOP_PAGE_SIZE", "24"))
SHOP_MAX_PAGE_SIZE = int(os.getenv("DJANGO_SHOP_MAX_PAGE_SIZE", "100"))

OAUTH_ENCRYPTION_KEY = os.getenv("DJANGO_OAUTH_ENCRYPTION_KEY", "")
OAUTH_STATE_TTL_SECONDS = int(os.getenv("DJANGO_OAUTH_STATE_TTL_SECONDS", "600"))
OAUTH_HTTP_TIMEOUT_SECONDS = float(os.getenv("DJANGO_OAUTH_HTTP_TIMEOUT_SECONDS", "10"))
OAUTH_CLOCK_SKEW_SECONDS = int(os.getenv("DJANGO_OAUTH_CLOCK_SKEW_SECONDS", "60"))
OAUTH_LOGIN_REDIRECT_URL = os.getenv("DJANGO_OAUTH_LOGIN_REDIRECT_URL", "/")
OAUTH_AUTO_CREATE_USERS = os.getenv("DJANGO_OAUTH_AUTO_CREATE_USERS", "true").lower() == "true"
OAUTH_STORE_PROVIDER_TOKENS = (
    os.getenv("DJANGO_OAUTH_STORE_PROVIDER_TOKENS", "false").lower() == "true"
)
OAUTH_USER_RESOLVER = os.getenv("DJANGO_OAUTH_USER_RESOLVER", "")

# The two transports that sit beside REST. Every app that publishes an API
# publishes all three off one service class, so these switches decide which
# *doors* are open, never what is behind them: turning either off unpublishes an
# endpoint and changes no behaviour.
GRAPHQL_ENABLED = os.getenv("DJANGO_GRAPHQL_ENABLED", "true").lower() == "true"
# The in-browser query editor. Handy in development, and an unauthenticated
# schema browser in production, so it follows DEBUG unless a deployment says
# otherwise.
GRAPHQL_GRAPHIQL = os.getenv("DJANGO_GRAPHQL_GRAPHIQL", str(DEBUG)).lower() == "true"
GRPC_ENABLED = os.getenv("DJANGO_GRPC_ENABLED", "true").lower() == "true"
GRPC_PORT = int(os.getenv("DJANGO_GRPC_PORT", "50051"))

INSTALLED_APPS = [
    # Unfold themes the admin by overriding its templates, so it has to be found
    # before the app whose templates it replaces. Its contrib apps are the same
    # arrangement for filters, form widgets and nested inlines.
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.inlines",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Always installed, and always first among this project's own apps. Every
    # other table here points at AUTH_USER_MODEL, and Django's advice is to own
    # that model from the first migration rather than swap it in later.
    ACCOUNTS_APP,
    *registered_app_configs(BASE_DIR / "src" / "config" / "api_registry.json"),
    *OAUTH_INSTALLED_APPS,
    *AUTH_INSTALLED_APPS,
    *CMS_INSTALLED_APPS,
    *NOTIFICATIONS_INSTALLED_APPS,
    *SHOP_INSTALLED_APPS,
    # The transports beside REST. Both are installed whether or not they are
    # published: `generateproto` and the schema check have to be able to run in a
    # deployment that serves neither.
    "strawberry_django",
    "django_socio_grpc",
]

# django-socio-grpc calls the hook once with the server it is starting, and
# `config.grpc` registers whichever apps this deployment installed.
GRPC_FRAMEWORK = {
    "ROOT_HANDLERS_HOOK": "config.grpc.grpc_handlers",
    "GRPC_CHANNEL_PORT": GRPC_PORT,
    # Asynchronous, and not only for throughput: on a synchronous server grpcio
    # hands the servicer a context whose trailing metadata is `None` until
    # something sets it, and django-socio-grpc reads it on every response. The
    # asyncio server starts it empty, which is what the library expects.
    "GRPC_ASYNC": True,
}

# The admin's appearance, all of it. The three callbacks are dotted paths rather
# than imports because settings are read before the app registry is ready, and
# each of them asks a question only the running project can answer: which apps
# are installed, whether this is production, and what the numbers are today.
UNFOLD = {
    "SITE_TITLE": "Django Ninja Starter",
    "SITE_HEADER": "Django Ninja Starter",
    "SITE_SUBHEADER": "Content, accounts and credentials",
    "SITE_SYMBOL": "rocket_launch",
    "SITE_URL": "/api/docs",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "SHOW_BACK_BUTTON": True,
    "ENVIRONMENT": "infrastructure.common.adminui.environment_badge",
    "DASHBOARD_CALLBACK": "infrastructure.common.adminui.dashboard",
    "BORDER_RADIUS": "6px",
    "COLORS": {
        "primary": {
            "50": "oklch(97.1% 0.014 254)",
            "100": "oklch(93.2% 0.032 255)",
            "200": "oklch(88.2% 0.059 254)",
            "300": "oklch(80.9% 0.105 252)",
            "400": "oklch(70.7% 0.165 254)",
            "500": "oklch(62.3% 0.214 259)",
            "600": "oklch(54.6% 0.245 262)",
            "700": "oklch(48.8% 0.243 264)",
            "800": "oklch(42.4% 0.199 265)",
            "900": "oklch(37.9% 0.146 265)",
            "950": "oklch(28.2% 0.091 267)",
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": "infrastructure.common.adminui.sidebar_navigation",
    },
    "COMMAND": {"search_models": True, "show_history": True},
}

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Project-wide overrides win over any app's, which is what lets the
        # dashboard replace the admin's front page without depending on where
        # this project's apps happen to sit in INSTALLED_APPS.
        "DIRS": [BASE_DIR / "src" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": os.getenv("DJANGO_DB_ENGINE", "django.db.backends.sqlite3"),
        "NAME": os.getenv("DJANGO_DB_NAME", BASE_DIR / "db.sqlite3"),
        "USER": os.getenv("DJANGO_DB_USER", ""),
        "PASSWORD": os.getenv("DJANGO_DB_PASSWORD", ""),
        "HOST": os.getenv("DJANGO_DB_HOST", ""),
        "PORT": os.getenv("DJANGO_DB_PORT", ""),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
