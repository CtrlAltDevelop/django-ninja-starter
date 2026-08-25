"""What the admin must not let a person do.

Two properties are worth holding onto here. An audit row cannot be created,
edited or deleted through a form, because a log anyone can adjust is not evidence
of anything. And no admin form may expose a stored secret -- the encrypted
provider tokens, the TOTP seed, the hashed client secret -- because the admin's
job is to say which account something belongs to, not to hand out a credential.

Django's own admin checks run here too. They are the only thing that catches a
column renamed out from under a `list_display`, which is otherwise a 500 on a
page nobody visits until they need it.
"""

from datetime import timedelta
from typing import Any

import pytest
from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.core import checks
from django.test import RequestFactory
from django.utils import timezone

from infrastructure.auth.core.models import AuthEvent, AuthEventType
from infrastructure.auth.twofactor.models import RecoveryCode
from infrastructure.common.admin import ReadOnlyAdmin, RevocableAdmin
from infrastructure.oauth.core.models import (
    OAuthAuditEvent,
    OAuthAuthorizationCode,
    SocialLoginAttempt,
)
from infrastructure.oauth.rotation.models import RefreshTokenReuseEvent
from infrastructure.oauth.session.models import SessionRevocation
from infrastructure.oauth.sliding.models import SlidingToken, SlidingTokenEvent

AUDIT_MODELS = [
    AuthEvent,
    RecoveryCode,
    OAuthAuditEvent,
    OAuthAuthorizationCode,
    SocialLoginAttempt,
    SlidingTokenEvent,
    SessionRevocation,
    RefreshTokenReuseEvent,
]
SECRET_FIELDS = {
    "access_token_encrypted",
    "refresh_token_encrypted",
    "secret_encrypted",
    "client_secret_hash",
}


@pytest.fixture
def staff_request(db: None) -> Any:
    """A request carrying what the admin's own middleware would have attached.

    An admin action reports back through the messages framework, so a bare
    RequestFactory request is not enough: it has no message store to write to.
    """
    request = RequestFactory().get("/admin/")
    request.user = get_user_model()._default_manager.create_superuser(
        username="root", email="root@example.test", password="irrelevant"
    )
    request.session = SessionStore()
    request._messages = FallbackStorage(request)
    return request


def test_the_admin_registrations_are_all_well_formed(db: None) -> None:
    """Django's admin checks, which catch a stale list_display or ordering."""
    errors = [message for message in checks.run_checks() if message.is_serious()]

    assert [f"{message.id}: {message.msg}" for message in errors] == []


def test_every_model_this_project_defines_is_registered() -> None:
    """An unregistered model is invisible to whoever has to support it."""
    registered = {model._meta.label for model in admin.site._registry}
    defined = {
        model._meta.label
        for model in apps.get_models()
        if model._meta.app_label.startswith(("auth_", "oauth_"))
    }

    assert defined - registered == set()


@pytest.mark.parametrize("model", AUDIT_MODELS, ids=lambda model: model._meta.label)
def test_an_audit_table_cannot_be_written_through_the_admin(staff_request: Any, model: Any) -> None:
    model_admin = admin.site._registry[model]

    assert isinstance(model_admin, ReadOnlyAdmin)
    assert model_admin.has_add_permission(staff_request) is False
    assert model_admin.has_change_permission(staff_request) is False
    assert model_admin.has_delete_permission(staff_request) is False


@pytest.mark.parametrize("model", AUDIT_MODELS, ids=lambda model: model._meta.label)
def test_an_audit_table_offers_no_editable_field(staff_request: Any, model: Any) -> None:
    model_admin = admin.site._registry[model]

    readonly = set(model_admin.get_readonly_fields(staff_request))

    assert {field.name for field in model._meta.fields} <= readonly


def test_no_admin_form_exposes_a_stored_secret() -> None:
    """Encrypted tokens and hashed secrets stay out of every form, on every model."""
    exposed = []
    for model, model_admin in admin.site._registry.items():
        secrets_on_model = SECRET_FIELDS & {field.name for field in model._meta.fields}
        if not secrets_on_model:
            continue
        excluded = set(model_admin.exclude or ())
        readonly = set(model_admin.get_readonly_fields(RequestFactory().get("/admin/")))
        for name in secrets_on_model:
            if name not in excluded and name not in readonly:
                exposed.append(f"{model._meta.label}.{name}")

    assert exposed == []


def test_revoking_from_the_admin_retires_the_credential(staff_request: Any) -> None:
    token = SlidingToken.objects.create(
        user=staff_request.user,
        token_hash="a" * 64,
        expires_at=timezone.now() + timedelta(minutes=10),
        absolute_expires_at=timezone.now() + timedelta(days=1),
    )
    model_admin = admin.site._registry[SlidingToken]
    assert isinstance(model_admin, RevocableAdmin)

    model_admin.revoke_selected(staff_request, SlidingToken.objects.all())

    token.refresh_from_db()
    assert token.revoked_at is not None
    assert token.revocation_reason == "admin"
    assert token.is_active is False


def test_revoking_something_already_revoked_changes_nothing(staff_request: Any) -> None:
    token = SlidingToken.objects.create(
        user=staff_request.user,
        token_hash="b" * 64,
        expires_at=timezone.now() + timedelta(minutes=10),
        absolute_expires_at=timezone.now() + timedelta(days=1),
    )
    token.revoke("logout")
    model_admin = admin.site._registry[SlidingToken]

    model_admin.revoke_selected(staff_request, SlidingToken.objects.all())

    token.refresh_from_db()
    assert token.revocation_reason == "logout"


def test_an_audit_row_written_by_the_application_is_still_visible(staff_request: Any) -> None:
    """Read-only means unwritable through the admin, not hidden from it."""
    AuthEvent.objects.create(event_type=AuthEventType.LOGIN_SUCCEEDED, method="password")
    model_admin = admin.site._registry[AuthEvent]

    assert model_admin.get_queryset(staff_request).count() == 1
