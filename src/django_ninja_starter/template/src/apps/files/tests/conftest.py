"""Shared fixtures: two accounts, a member of staff, and storage in memory."""

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"x" * 64


@pytest.fixture(autouse=True)
def _memory_storage(settings: Any) -> None:
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    }


@pytest.fixture
def alice(transactional_db: None) -> Any:
    return get_user_model().objects.create_user(username="alice", email="alice@example.test")


@pytest.fixture
def bob(transactional_db: None) -> Any:
    return get_user_model().objects.create_user(username="bob", email="bob@example.test")


@pytest.fixture
def staff(transactional_db: None) -> Any:
    return get_user_model().objects.create_user(
        username="staff", email="staff@example.test", is_staff=True
    )


def access_token(user: Any) -> str:
    from infrastructure.auth.core.sessions import issue_credentials

    return issue_credentials(RequestFactory().post("/"), user, method="password").access_token


def bearer(user: Any) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token(user)}"}
