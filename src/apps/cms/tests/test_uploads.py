"""Turning a picked file into the address a media field stores.

Everything here writes through Django's configured storage, so every test runs
against a temporary ``MEDIA_ROOT`` -- otherwise a suite run leaves files in the
project's own media folder, which is the sort of thing nobody notices until a
deployment ships them.
"""

from pathlib import Path
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.cms import uploads
from apps.cms.fields import FieldType


@pytest.fixture(autouse=True)
def media(tmp_path: Path) -> Any:
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        yield tmp_path


def _file(name: str, content: bytes = b"x") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, content)


class TestNaming:
    def test_the_editors_own_name_survives_in_a_readable_form(self) -> None:
        """A folder of `a1b2c3.png` is unsearchable for whoever uploaded it."""
        name = uploads.safe_name("Pricing Hero.PNG")

        assert name.startswith("Pricing-Hero-")
        assert name.endswith(".png")

    def test_two_uploads_of_the_same_name_do_not_collide(self) -> None:
        assert uploads.safe_name("logo.png") != uploads.safe_name("logo.png")

    def test_anything_that_would_need_escaping_is_replaced(self) -> None:
        name = uploads.safe_name("final (2)+copy.png")

        assert " " not in name
        assert "(" not in name
        assert "+" not in name

    def test_a_path_cannot_be_smuggled_in_through_the_name(self) -> None:
        name = uploads.safe_name("../../etc/passwd")

        assert "/" not in name
        assert not name.startswith(".")

    def test_a_name_with_no_extension_still_gets_one_of_a_kind(self) -> None:
        assert uploads.safe_name("README").startswith("README-")

    def test_an_unnamed_file_is_still_named(self) -> None:
        assert uploads.safe_name("").startswith("file-")


class TestExtensionOf:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [("a.PNG", ".png"), ("a.tar.gz", ".gz"), ("README", ""), ("", "")],
    )
    def test_it_reads_the_last_extension_lower_cased(self, name: str, expected: str) -> None:
        assert uploads.extension_of(name) == expected


class TestRefusals:
    def test_an_extension_the_type_does_not_take_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="Allowed: .avif"):
            uploads.check(_file("script.exe"), FieldType.IMAGE)

    def test_a_video_is_not_an_image(self) -> None:
        with pytest.raises(ValidationError, match="not one this field takes"):
            uploads.check(_file("clip.mp4"), FieldType.IMAGE)

    def test_a_file_field_takes_whatever_it_is_given(self) -> None:
        """That is what the generic type is for: a PDF, a zip, a spreadsheet."""
        uploads.check(_file("terms.pdf"), FieldType.FILE)

    @override_settings(CMS_MAX_UPLOAD_BYTES=10)
    def test_a_file_over_the_limit_is_refused_before_it_is_written(self) -> None:
        with pytest.raises(ValidationError, match="The limit is"):
            uploads.check(_file("big.png", b"x" * 11), FieldType.IMAGE)

    @override_settings(CMS_MAX_UPLOAD_BYTES=0)
    def test_zero_means_no_limit(self) -> None:
        """For a deployment whose proxy or bucket already imposes one."""
        uploads.check(_file("big.png", b"x" * 5_000), FieldType.IMAGE)


class TestStoring:
    def test_it_returns_the_address_the_file_is_served_under(self) -> None:
        url = uploads.store(_file("hero.png"), FieldType.IMAGE)

        assert url.startswith("/media/cms/uploads/image/hero-")
        assert url.endswith(".png")

    def test_files_are_grouped_by_type(self, media: Path) -> None:
        uploads.store(_file("clip.mp4"), FieldType.VIDEO)

        assert (media / "cms" / "uploads" / "video").is_dir()

    @override_settings(CMS_UPLOAD_PATH="content/files")
    def test_the_folder_is_a_setting(self) -> None:
        assert "/content/files/" in uploads.store(_file("a.png"), FieldType.IMAGE)

    def test_a_refused_file_never_reaches_storage(self, media: Path) -> None:
        with pytest.raises(ValidationError):
            uploads.store(_file("script.exe"), FieldType.IMAGE)

        assert not (media / "cms").exists()
