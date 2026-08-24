import os

os.environ.setdefault("DJANGO_OAUTH_MODE", "all")
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
