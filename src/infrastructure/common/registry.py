"""Read and validate the declarative API registry."""

import json
import re
from pathlib import Path
from typing import TypedDict

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

API_VERSION_PATTERN = re.compile(r"^v\d+(?:\.\d+){0,2}$")


class ApiRoute(TypedDict):
    app_config: str
    prefix: str
    router: str
    tag: str


class ApiVersion(TypedDict):
    routes: list[ApiRoute]


type ApiRegistry = dict[str, ApiVersion]


def api_registry_path() -> Path:
    """Return the registry path for the active project."""
    return Path(settings.BASE_DIR) / "src" / "config" / "api_registry.json"


def normalize_api_version(version: str) -> str:
    """Normalize a version such as `1` or `v1.2` to its URL label."""
    normalized = version.lower()
    if not normalized.startswith("v"):
        normalized = f"v{normalized}"
    if not API_VERSION_PATTERN.fullmatch(normalized):
        raise ValueError("version must look like v1, v1.1, or v1.1.0")
    return normalized


def api_version_number(version: str) -> str:
    """Convert a URL version label to a three-component API version."""
    parts = normalize_api_version(version)[1:].split(".")
    return ".".join([*parts, *(["0"] * (3 - len(parts)))])


def load_api_registry(path: Path | None = None) -> ApiRegistry:
    """Load the API registry and reject malformed top-level data."""
    registry_path = path or api_registry_path()
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ImproperlyConfigured(f"Cannot load API registry: {error}") from error

    if not isinstance(data, dict) or not data:
        raise ImproperlyConfigured("API registry must contain at least one version")
    for version, configuration in data.items():
        try:
            normalize_api_version(version)
        except ValueError as error:
            raise ImproperlyConfigured(str(error)) from error
        if not isinstance(configuration, dict) or not isinstance(configuration.get("routes"), list):
            raise ImproperlyConfigured(f"API registry version {version} requires a routes list")
    return data


def registered_app_configs(path: Path | None = None) -> list[str]:
    """Return unique Django app configurations used by registered routes."""
    app_configs: list[str] = []
    for configuration in load_api_registry(path).values():
        for route in configuration["routes"]:
            app_config = route["app_config"]
            if app_config not in app_configs:
                app_configs.append(app_config)
    return app_configs
