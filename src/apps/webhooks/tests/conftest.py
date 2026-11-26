"""Fixtures for the webhook tests. Collection stops if the app is not installed."""

from collections.abc import Iterator
from typing import Any
from unittest import mock

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import RequestFactory

collect_ignore_glob = [] if django_apps.is_installed("apps.webhooks") else ["*"]

PUBLIC_IP = "93.184.216.34"


def resolving_to(address: str) -> Any:
    return mock.patch(
        "apps.webhooks.services.socket.getaddrinfo",
        return_value=[(2, 1, 6, "", (address, 443))],
    )


@pytest.fixture(autouse=True)
def public_dns() -> Iterator[None]:
    """No test reaches a real resolver; every host is public unless a test says not."""
    with resolving_to(PUBLIC_IP):
        yield


def access_token(user: Any) -> str:
    from infrastructure.auth.core.sessions import issue_credentials

    token: str = issue_credentials(RequestFactory().post("/"), user, method="password").access_token
    return token


@pytest.fixture
def alice(db: None) -> Any:
    return get_user_model().objects.create_user(username="alice", email="alice@example.test")


@pytest.fixture
def bob(db: None) -> Any:
    return get_user_model().objects.create_user(username="bob", email="bob@example.test")
