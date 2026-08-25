import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

from infrastructure.common.registry import registered_app_configs

BASE_DIR = Path(__file__).resolve().parents[3]
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "unsafe-development-key")
DEBUG = False
ALLOWED_HOSTS = [
    host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if host.strip()
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
OAUTH_INSTALLED_APPS = []
if OAUTH_MODE != "none" or OAUTH_PROVIDERS:
    OAUTH_INSTALLED_APPS.append("infrastructure.oauth.core.apps.OAuthCoreConfig")
OAUTH_INSTALLED_APPS.extend(OAUTH_MODE_APPS[OAUTH_MODE])
OAUTH_INSTALLED_APPS.extend(OAUTH_PROVIDER_APPS[provider] for provider in OAUTH_PROVIDERS)

OAUTH_PROVIDER_ROUTERS = [
    {
        "prefix": f"/oauth/{provider}",
        "router": f"infrastructure.oauth.{provider}.api.router",
        "tag": f"OAuth - {provider.title()}",
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

AUTH_METHOD_ROUTERS = [
    {
        "prefix": f"/auth/{method.replace('_', '-')}",
        "router": f"infrastructure.auth.{method}.api.router",
        "tag": f"Auth - {method.replace('_', ' ').title()}",
    }
    for method in AUTH_METHODS
]
if AUTH_SECOND_FACTORS:
    AUTH_METHOD_ROUTERS.append(
        {
            "prefix": "/auth/2fa",
            "router": "infrastructure.auth.twofactor.api.router",
            "tag": "Auth - Two Factor",
        }
    )

AUTH_TOKEN_MODES = {"none", "sliding", "session", "rotation"}
AUTH_TOKEN_MODE = os.getenv("DJANGO_AUTH_TOKEN_MODE", "").lower() or (
    "rotation" if OAUTH_MODE == "all" else OAUTH_MODE
)
if AUTH_TOKEN_MODE not in AUTH_TOKEN_MODES:
    raise ImproperlyConfigured(
        "DJANGO_AUTH_TOKEN_MODE must be one of: none, sliding, session, rotation"
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
AUTH_TOTP_ISSUER = os.getenv("DJANGO_AUTH_TOTP_ISSUER", "{{ project_title }}")
AUTH_RECOVERY_CODE_COUNT = int(os.getenv("DJANGO_AUTH_RECOVERY_CODE_COUNT", "10"))

# Signed credentials. HS256 needs only DJANGO_SECRET_KEY to work out of the box;
# an RS*/ES* algorithm needs a real key pair, which the system checks insist on
# rather than silently falling back to something weaker.
AUTH_JWT_ALGORITHM = os.getenv("DJANGO_AUTH_JWT_ALGORITHM", "HS256").upper()
AUTH_JWT_SIGNING_KEY = os.getenv("DJANGO_AUTH_JWT_SIGNING_KEY", "")
AUTH_JWT_VERIFYING_KEY = os.getenv("DJANGO_AUTH_JWT_VERIFYING_KEY", "")
AUTH_JWT_ISSUER = os.getenv("DJANGO_AUTH_JWT_ISSUER", "{{ project_name }}")
AUTH_JWT_AUDIENCE = os.getenv("DJANGO_AUTH_JWT_AUDIENCE", "")
AUTH_JWT_LEEWAY_SECONDS = int(os.getenv("DJANGO_AUTH_JWT_LEEWAY_SECONDS", "30"))

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

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    *registered_app_configs(BASE_DIR / "src" / "config" / "api_registry.json"),
    *OAUTH_INSTALLED_APPS,
    *AUTH_INSTALLED_APPS,
]

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
        "DIRS": [],
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
