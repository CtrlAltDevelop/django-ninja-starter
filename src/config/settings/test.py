import os

os.environ.setdefault("DJANGO_CMS_ENABLED", "true")
os.environ.setdefault("DJANGO_NOTIFICATIONS_ENABLED", "true")
os.environ.setdefault("DJANGO_OAUTH_MODE", "all")
os.environ.setdefault("DJANGO_AUTH_TOKEN_MODE", "rotation")
os.environ.setdefault("DJANGO_AUTH_METHODS", "password,email_code,sms_code,magic_link")
os.environ.setdefault("DJANGO_AUTH_SECOND_FACTORS", "totp,sms,email,recovery")
os.environ.setdefault(
    "DJANGO_AUTH_CHALLENGE_STORE",
    "infrastructure.auth.core.challenges.LocMemChallengeStore",
)
os.environ.setdefault(
    "DJANGO_AUTH_SMS_BACKEND", "infrastructure.auth.core.delivery.LocMemSmsBackend"
)
os.environ.setdefault(
    "DJANGO_AUTH_EMAIL_BACKEND", "infrastructure.auth.core.delivery.LocMemEmailBackend"
)
os.environ.setdefault("DJANGO_AUTH_MAGIC_LINK_BASE_URL", "https://example.test/auth/link")
os.environ.setdefault("DJANGO_AUTH_PASSWORD_RESET_BASE_URL", "https://example.test/auth/reset")
os.environ.setdefault("DJANGO_AUTH_RESEND_COOLDOWN_SECONDS", "0")
os.environ.setdefault(
    "DJANGO_AUTH_JWT_SIGNING_KEY", "test-only-jwt-signing-key-of-sufficient-length"
)
# The declared settings contracts are real requirements, so the suite satisfies
# them the way a deployment would. The challenge store is the one deliberate
# exception: tests need the in-process one, and it warns about itself.
os.environ.setdefault("DJANGO_AUTH_EMAIL_FROM", "sign-in@example.test")
os.environ.setdefault("DJANGO_AUTH_SMS_FROM", "+15555550100")
os.environ.setdefault("DJANGO_OAUTH_PROVIDERS", "google,apple,microsoft,github")
for key, value in {
    "GOOGLE_OAUTH_CLIENT_ID": "test-google-client",
    "GOOGLE_OAUTH_CLIENT_SECRET": "test-google-secret",
    "GOOGLE_OAUTH_REDIRECT_URI": "https://example.test/api/v1/oauth/google/callback",
    "APPLE_OAUTH_CLIENT_ID": "test.apple.service",
    "APPLE_OAUTH_TEAM_ID": "TESTTEAM",
    "APPLE_OAUTH_KEY_ID": "TESTKEY",
    "APPLE_OAUTH_PRIVATE_KEY": "test-private-key",
    "APPLE_OAUTH_REDIRECT_URI": "https://example.test/api/v1/oauth/apple/callback",
    "MICROSOFT_OAUTH_CLIENT_ID": "test-microsoft-client",
    "MICROSOFT_OAUTH_CLIENT_SECRET": "test-microsoft-secret",
    "MICROSOFT_OAUTH_REDIRECT_URI": "https://example.test/api/v1/oauth/microsoft/callback",
    "GITHUB_OAUTH_CLIENT_ID": "test-github-client",
    "GITHUB_OAUTH_CLIENT_SECRET": "test-github-secret",
    "GITHUB_OAUTH_REDIRECT_URI": "https://example.test/api/v1/oauth/github/callback",
}.items():
    os.environ.setdefault(key, value)

from config.settings.base import *  # noqa: E402,F403

SECRET_KEY = "test-only-secret-key"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
