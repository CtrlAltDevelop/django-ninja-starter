"""Every decision the files app makes, for all three transports.

* **What may be stored.** A size limit checked before anything is read, then
  the content sniffed from its first bytes. The extension must be one the
  sniffed type is known by: a PNG named ``.pdf`` and an HTML page named ``.txt``
  are both refused, and nothing that is not on :data:`ALLOWED_TYPES` gets in.
* **Where it goes.** A random key under ``FILES_UPLOAD_PATH``, grouped by day.
  The uploader's name is kept on the row, never in the key.
* **Who may see it.** The owner, and staff. Anybody else gets the same "not
  found" a missing id gets, so an id never confirms another account's file.
"""

import posixpath
import uuid
from typing import IO, Any
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.db.models import QuerySet
from django.utils import timezone

from apps.files.models import StoredFile
from infrastructure.common.uploads import SNIFF_BYTES, check_size, extension_of, sniff

DEFAULT_PAGE = 50
MAX_PAGE = 100

#: Sniffed type -> the extensions a file of that type may be named with. Office
#: documents are zip archives, so they sniff as one. Nothing a browser executes
#: is here: text is served as ``text/plain`` whatever it is named, and ``.html``,
#: ``.svg`` and ``.xml`` are not on any list.
ALLOWED_TYPES: dict[str, tuple[str, ...]] = {
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/gif": (".gif",),
    "image/webp": (".webp",),
    "application/pdf": (".pdf",),
    "application/zip": (".zip", ".docx", ".xlsx", ".pptx"),
    "application/gzip": (".gz",),
    "text/plain": (".txt", ".csv", ".json", ".md", ".log"),
}


class FileNotFound(LookupError):
    """No such file, or not one this account may see. Deliberately the same thing."""


def upload_root() -> str:
    return str(settings.FILES_UPLOAD_PATH).strip("/")


def window(limit: int, offset: int) -> tuple[int, int]:
    """Clamp a page request to something the database should be asked for."""
    return max(1, min(limit, MAX_PAGE)), max(0, offset)


def payload(row: StoredFile) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "owner_id": str(row.owner_id),
        "name": row.name,
        "content_type": row.content_type,
        "size": row.size,
        "created_at": row.created_at.isoformat(),
    }


def _visible(user: Any) -> QuerySet[StoredFile]:
    if user.is_staff:
        return StoredFile.objects.all()
    return StoredFile.objects.filter(owner=user)


def _row(user: Any, file_id: UUID | str) -> StoredFile:
    try:
        return _visible(user).get(pk=UUID(str(file_id)))
    except (StoredFile.DoesNotExist, ValueError):
        raise FileNotFound("No such file.") from None


def validate(name: str, content: IO[bytes], size: int) -> str:
    """Return the sniffed content type, or raise :class:`ValidationError`."""
    check_size(size, int(settings.FILES_MAX_UPLOAD_BYTES))
    content.seek(0)
    content_type = sniff(content.read(SNIFF_BYTES))
    content.seek(0)
    if content_type not in ALLOWED_TYPES:
        raise ValidationError("That kind of file is not accepted.")
    extension = extension_of(name)
    if extension not in ALLOWED_TYPES[content_type]:
        raise ValidationError(
            f"The content is {content_type}, which may be named "
            f"{', '.join(ALLOWED_TYPES[content_type])}, not {extension or 'with no extension'}."
        )
    return content_type


def upload(user: Any, name: str, content: IO[bytes], size: int) -> dict[str, Any]:
    """Validate and store one file owned by ``user``."""
    content_type = validate(name, content, size)
    key = posixpath.join(
        upload_root(), f"{timezone.now():%Y/%m/%d}", uuid.uuid4().hex + extension_of(name)
    )
    stored = default_storage.save(key, content)
    try:
        row = StoredFile.objects.create(
            owner=user,
            name=posixpath.basename(name.replace("\\", "/"))[:255] or "file",
            storage_key=stored,
            content_type=content_type,
            size=size,
        )
    except Exception:
        default_storage.delete(stored)
        raise
    return payload(row)


def list_files(user: Any, *, limit: int = DEFAULT_PAGE, offset: int = 0) -> dict[str, Any]:
    limit, offset = window(limit, offset)
    rows = _visible(user)
    return {
        "files": [payload(row) for row in rows[offset : offset + limit]],
        "total": rows.count(),
        "limit": limit,
        "offset": offset,
    }


def get(user: Any, file_id: UUID | str) -> dict[str, Any]:
    return payload(_row(user, file_id))


def open_file(user: Any, file_id: UUID | str) -> tuple[dict[str, Any], IO[bytes]]:
    """The file's metadata and an open binary handle the caller must close."""
    row = _row(user, file_id)
    return payload(row), default_storage.open(row.storage_key, "rb")


def read(user: Any, file_id: UUID | str) -> tuple[dict[str, Any], bytes]:
    meta, handle = open_file(user, file_id)
    with handle:
        return meta, handle.read()


def delete(user: Any, file_id: UUID | str) -> None:
    """Remove the row; its ``post_delete`` signal removes the bytes."""
    _row(user, file_id).delete()
