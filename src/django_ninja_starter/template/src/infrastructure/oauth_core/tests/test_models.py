from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from infrastructure.oauth_core.models import OAuthAuthorizationCode, OAuthClient
from infrastructure.oauth_core.tokens import generate_token, hash_token
from infrastructure.oauth_rotation.models import (
    RotatingAccessToken,
    RotatingRefreshToken,
    TokenFamily,
)
from infrastructure.oauth_session.models import OAuthSession, SessionAccessToken
from infrastructure.oauth_sliding.models import SlidingToken

pytestmark = pytest.mark.django_db


def _user() -> Any:
    return get_user_model().objects.create_user(username="oauth-user", password="test-password")


def test_tokens_are_random_and_stored_as_hashes() -> None:
    first = generate_token()
    second = generate_token()

    assert first != second
    assert len(hash_token(first)) == 64
    assert first not in hash_token(first)


def test_confidential_client_requires_hashed_secret() -> None:
    client = OAuthClient(
        name="Server",
        client_id="server-client",
        client_type=OAuthClient.ClientType.CONFIDENTIAL,
        token_endpoint_auth_method=OAuthClient.TokenEndpointAuthMethod.CLIENT_SECRET_BASIC,
    )

    with pytest.raises(ValidationError, match="hashed client secret"):
        client.full_clean()


def test_authorization_code_tracks_consumption_and_expiry() -> None:
    user = _user()
    client = OAuthClient.objects.create(name="Public", client_id="public-client")
    code = OAuthAuthorizationCode.objects.create(
        code_hash=hash_token(generate_token()),
        user=user,
        client=client,
        redirect_uri="https://client.example/callback",
        code_challenge="challenge",
        expires_at=timezone.now() + timedelta(minutes=5),
    )

    assert code.is_active
    code.consumed_at = timezone.now()
    assert not code.is_active


def test_sliding_token_extends_idle_expiry_within_absolute_limit() -> None:
    user = _user()
    now = timezone.now()
    token = SlidingToken.objects.create(
        user=user,
        token_hash=hash_token(generate_token()),
        expires_at=now + timedelta(seconds=5),
        absolute_expires_at=now + timedelta(minutes=2),
        idle_timeout_seconds=60,
    )

    original_expiry = token.expires_at
    assert token.slide()
    assert token.expires_at > original_expiry
    assert token.expires_at <= token.absolute_expires_at
    assert token.slide_count == 1


def test_session_revocation_invalidates_access_tokens() -> None:
    user = _user()
    expires_at = timezone.now() + timedelta(hours=1)
    session = OAuthSession.objects.create(
        user=user,
        session_key_hash=hash_token(generate_token()),
        expires_at=expires_at,
    )
    access = SessionAccessToken.objects.create(
        user=user,
        session=session,
        token_hash=hash_token(generate_token()),
        expires_at=expires_at,
    )

    assert access.is_active
    session.revoke("user_logout")
    access.refresh_from_db()
    assert access.revoked_at is not None
    assert not session.is_active


def test_refresh_rotation_retains_ancestry_and_revokes_family_on_reuse() -> None:
    user = _user()
    expires_at = timezone.now() + timedelta(days=1)
    family = TokenFamily.objects.create(user=user, expires_at=expires_at)
    first = RotatingRefreshToken.objects.create(
        user=user,
        family=family,
        token_hash=hash_token(generate_token()),
        expires_at=expires_at,
        rotation_index=0,
    )
    replacement = RotatingRefreshToken.objects.create(
        user=user,
        family=family,
        parent=first,
        token_hash=hash_token(generate_token()),
        expires_at=expires_at,
        rotation_index=1,
    )
    access = RotatingAccessToken.objects.create(
        user=user,
        family=family,
        issued_from=replacement,
        token_hash=hash_token(generate_token()),
        expires_at=timezone.now() + timedelta(minutes=5),
    )

    first.mark_used(replacement)
    assert first.replaced_by == replacement
    first.mark_reuse()
    family.refresh_from_db()
    replacement.refresh_from_db()
    access.refresh_from_db()
    assert family.reuse_detected_at is not None
    assert replacement.revoked_at is not None
    assert access.revoked_at is not None
