"""The shared sniffing and naming helpers."""

import pytest
from django.core.exceptions import ValidationError

from infrastructure.common.uploads import check_size, extension_of, safe_name, sniff


@pytest.mark.parametrize(
    ("head", "expected"),
    [
        (b"\x89PNG\r\n\x1a\nrest", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"GIF89a", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        (b"%PDF-1.4", "application/pdf"),
        (b"PK\x03\x04", "application/zip"),
        (b"\x1f\x8b\x08", "application/gzip"),
        (b"a,b\n1,2\n", "text/plain"),
        ("héllo".encode()[:2], "text/plain"),
        (b"MZ\x90\x00", None),
        (b"\xff\xfe\xfd\xfc garbage" * 10, None),
        (b"", None),
    ],
)
def test_sniff_reads_the_content_not_the_name(head: bytes, expected: str | None) -> None:
    assert sniff(head) == expected


def test_size_limit_zero_means_none() -> None:
    check_size(10**12, 0)
    with pytest.raises(ValidationError):
        check_size(2048, 1024)


def test_safe_name_and_extension() -> None:
    assert safe_name("../../etc/Pass wd.PNG").endswith(".png")
    assert "/" not in safe_name("../../x.png")
    assert extension_of("Shot.PNG") == ".png"
    assert extension_of("noext") == ""
