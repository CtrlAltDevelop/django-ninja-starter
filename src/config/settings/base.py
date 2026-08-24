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
    "sliding": ["infrastructure.oauth_sliding.apps.OAuthSlidingConfig"],
    "session": ["infrastructure.oauth_session.apps.OAuthSessionConfig"],
    "rotation": ["infrastructure.oauth_rotation.apps.OAuthRotationConfig"],
    "all": [
        "infrastructure.oauth_sliding.apps.OAuthSlidingConfig",
        "infrastructure.oauth_session.apps.OAuthSessionConfig",
        "infrastructure.oauth_rotation.apps.OAuthRotationConfig",
    ],
}
if OAUTH_MODE not in OAUTH_MODE_APPS:
    raise ImproperlyConfigured(
        "DJANGO_OAUTH_MODE must be one of: none, sliding, session, rotation, all"
    )
OAUTH_PROVIDER_APPS = {
    "google": "infrastructure.oauth_google.apps.OAuthGoogleConfig",
    "apple": "infrastructure.oauth_apple.apps.OAuthAppleConfig",
    "microsoft": "infrastructure.oauth_microsoft.apps.OAuthMicrosoftConfig",
    "github": "infrastructure.oauth_github.apps.OAuthGitHubConfig",
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
    OAUTH_INSTALLED_APPS.append("infrastructure.oauth_core.apps.OAuthCoreConfig")
OAUTH_INSTALLED_APPS.extend(OAUTH_MODE_APPS[OAUTH_MODE])
OAUTH_INSTALLED_APPS.extend(OAUTH_PROVIDER_APPS[provider] for provider in OAUTH_PROVIDERS)

OAUTH_PROVIDER_ROUTERS = [
    {
        "prefix": f"/oauth/{provider}",
        "router": f"infrastructure.oauth_{provider}.api.router",
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
