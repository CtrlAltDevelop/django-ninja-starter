"""What may be stored, where it goes, and who may see it."""

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from apps.files import services
from apps.files.models import StoredFile
from apps.files.tests.conftest import PDF, PNG


def _upload(user: Any, name: str = "shot.png", content: bytes = PNG) -> dict[str, Any]:
    return services.upload(user, name, ContentFile(content), len(content))


def test_a_file_is_stored_under_a_random_key_and_keeps_its_name(alice: Any) -> None:
    row = _upload(alice, "../My Shot.png")
    stored = StoredFile.objects.get(pk=row["id"])

    assert row["name"] == "My Shot.png"
    assert row["content_type"] == "image/png"
    assert stored.storage_key.startswith("files/")
    assert "Shot" not in stored.storage_key
    assert stored.storage_key.endswith(".png")
    assert default_storage.exists(stored.storage_key)


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("shot.pdf", PNG),  # renamed image
        ("page.txt", b"\x00\x01binary"),  # binary named as text
        ("page.html", b"<script>alert(1)</script>"),  # text, but not an allowed name
        ("evil.exe", b"MZ\x90\x00" + b"\x00" * 10),  # unknown content
        ("noext", PDF),
    ],
)
def test_content_that_does_not_match_its_name_is_refused(
    alice: Any, name: str, content: bytes
) -> None:
    with pytest.raises(ValidationError):
        _upload(alice, name, content)
    assert not StoredFile.objects.exists()


def test_an_oversized_file_is_refused_before_it_is_read(alice: Any, settings: Any) -> None:
    settings.FILES_MAX_UPLOAD_BYTES = 10

    with pytest.raises(ValidationError, match="limit"):
        _upload(alice)


def test_owners_see_their_own_and_staff_see_everything(alice: Any, bob: Any, staff: Any) -> None:
    mine = _upload(alice)
    _upload(bob, "b.pdf", PDF)

    assert [row["id"] for row in services.list_files(alice)["files"]] == [mine["id"]]
    assert services.list_files(staff)["total"] == 2
    with pytest.raises(services.FileNotFound):
        services.get(bob, mine["id"])
    with pytest.raises(services.FileNotFound):
        services.delete(bob, mine["id"])
    with pytest.raises(services.FileNotFound):
        services.get(alice, "not-a-uuid")
    assert services.read(staff, mine["id"])[1] == PNG


def test_deleting_removes_the_bytes(alice: Any) -> None:
    row = _upload(alice)
    key = StoredFile.objects.get(pk=row["id"]).storage_key

    services.delete(alice, row["id"])

    assert not StoredFile.objects.exists()
    assert not default_storage.exists(key)


def test_the_page_is_clamped(alice: Any) -> None:
    assert services.window(10_000, -5) == (services.MAX_PAGE, 0)
