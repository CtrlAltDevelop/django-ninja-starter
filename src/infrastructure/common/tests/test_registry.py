import json
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

from infrastructure.common.registry import (
    api_version_number,
    load_api_registry,
    normalize_api_version,
    registered_app_configs,
)


@pytest.mark.parametrize(
    ("value", "normalized", "number"),
    [("1", "v1", "1.0.0"), ("v2.1", "v2.1", "2.1.0"), ("V3.2.1", "v3.2.1", "3.2.1")],
)
def test_api_version_normalization(value: str, normalized: str, number: str) -> None:
    assert normalize_api_version(value) == normalized
    assert api_version_number(value) == number


def test_invalid_api_version_is_rejected() -> None:
    with pytest.raises(ValueError, match="version must look like"):
        normalize_api_version("latest")


def test_registry_returns_unique_app_configs(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "v1": {
                    "routes": [
                        {
                            "app_config": "apps.users.apps.UsersConfig",
                            "prefix": "/users",
                            "router": "apps.users.api.v1.router",
                            "tag": "Users",
                        },
                        {
                            "app_config": "apps.users.apps.UsersConfig",
                            "prefix": "/profiles",
                            "router": "apps.users.api.profiles.router",
                            "tag": "Profiles",
                        },
                    ]
                }
            }
        )
    )

    assert registered_app_configs(registry_path) == ["apps.users.apps.UsersConfig"]


def test_empty_registry_is_rejected(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    registry_path.write_text("{}")

    with pytest.raises(ImproperlyConfigured, match="at least one version"):
        load_api_registry(registry_path)
