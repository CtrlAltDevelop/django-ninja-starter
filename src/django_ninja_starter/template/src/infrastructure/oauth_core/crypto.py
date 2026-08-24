"""Encryption helpers for OAuth credentials that must remain recoverable."""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet() -> Fernet:
    configured_key = getattr(settings, "OAUTH_ENCRYPTION_KEY", "")
    key = (
        configured_key.encode()
        if configured_key
        else base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    )
    try:
        return Fernet(key)
    except (TypeError, ValueError) as error:
        raise ImproperlyConfigured("DJANGO_OAUTH_ENCRYPTION_KEY must be a Fernet key") from error


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as error:
        raise ValueError("OAuth credential could not be decrypted") from error
