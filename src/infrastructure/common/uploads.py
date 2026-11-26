"""The parts of accepting a file that do not depend on which app is accepting it.

Shared by ``apps.support`` (attachments) and ``apps.files`` (owned files), so the
two agree on what a safe name is, how a size limit is worded and what a file's
first bytes say it is.
"""

import re
import unicodedata
import uuid
from pathlib import PurePosixPath

from django.core.exceptions import ValidationError

#: What survives of an uploaded file's own name. Everything else becomes a
#: hyphen, because the name ends up in a URL and a storage key, and
#: "screenshot (2)+copy.png" is a different string in each of the places it is
#: written.
UNSAFE = re.compile(r"[^a-zA-Z0-9._-]+")

#: How many leading bytes :func:`sniff` needs. Enough for every signature below
#: and for a fair look at whether a text file is text.
SNIFF_BYTES = 2048

#: Magic numbers, checked in order. ``RIFF`` needs a second look (WEBP vs WAV),
#: so it is handled in :func:`sniff` rather than here.
SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
    (b"\x1f\x8b", "application/gzip"),
)


def safe_name(name: str) -> str:
    """A storage-safe, URL-safe version of an uploaded file's own name.

    The stem is kept -- shortened, ASCII-folded and stripped of anything that
    would need escaping -- because a folder of ``a1b2c3.png`` is unsearchable
    for the agent looking for the screenshot a client sent this morning.
    Uniqueness comes from a suffix rather than from the name, so two people
    sending ``screenshot.png`` do not overwrite each other.
    """
    stem = PurePosixPath(name or "file").name
    folded = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    cleaned = UNSAFE.sub("-", folded).strip("-.") or "file"
    root, _, extension = cleaned.rpartition(".")
    root = (root or cleaned)[:60].strip("-") or "file"
    suffix = uuid.uuid4().hex[:8]
    return f"{root}-{suffix}.{extension.lower()}" if extension else f"{root}-{suffix}"


def extension_of(name: str) -> str:
    """``"Shot.PNG"`` -> ``".png"``. Empty when the name has no extension at all."""
    _, dot, extension = PurePosixPath(name or "").name.rpartition(".")
    return f".{extension.lower()}" if dot else ""


def check_size(size: int, limit: int) -> None:
    """Refuse a file over ``limit`` bytes. A limit of ``0`` means none."""
    if limit and size > limit:
        raise ValidationError(f"That file is {size // 1024} KB. The limit is {limit // 1024} KB.")


def sniff(head: bytes) -> str | None:
    """What a file's first bytes say it is, or ``None`` when they say nothing known.

    Text is anything that decodes as UTF-8 with no NUL byte in it -- a CSV, a log
    and a JSON file all look the same from here, which is why the caller still
    checks the extension against what was sniffed.
    """
    for magic, content_type in SIGNATURES:
        if head.startswith(magic):
            return content_type
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if not head or b"\x00" in head:
        return None
    try:
        # A multi-byte character cut off at the end of the sample is still text.
        head.decode("utf-8")
    except UnicodeDecodeError as error:
        if error.start < len(head) - 3:
            return None
    return "text/plain"
