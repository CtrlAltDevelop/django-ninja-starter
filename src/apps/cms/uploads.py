"""Turning a file an editor picked into the URL a media field stores.

A media field's value is an address, never a file. That is what lets the same
field hold a picture somebody uploaded here and a picture already on a CDN, and
it is why the content screen offers an upload button *and* a URL box side by
side rather than choosing between them: the two are the same answer arrived at
differently, and an editor should not have to know which one the project set up.

Uploads go through Django's configured storage, so a project that has pointed
``STORAGES["default"]`` at S3, GCS or anything else gets that without this app
knowing. What comes back is ``storage.url(...)``, which is the address that
storage serves the file under -- a local path in development, a bucket URL in
production, and neither of them this module's business.

Two refusals live here, both because the alternative is a bad file already
written to storage before anybody notices:

* **an extension the type does not accept**, checked against
  :data:`~apps.cms.fields.UPLOAD_EXTENSIONS`. The browser's reported MIME type
  is the client's claim; the extension is at least the name the file will be
  served under, and the one a static host will pick a ``Content-Type`` from.
* **a file over ``CMS_MAX_UPLOAD_BYTES``**, checked before the read rather than
  after, so an oversized upload costs a rejection and not a disk.
"""

import posixpath
import re
import unicodedata
import uuid
from pathlib import PurePosixPath
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage

from apps.cms.fields import UPLOAD_EXTENSIONS

#: What survives of an uploaded file's own name. Everything else becomes a
#: hyphen, because the name ends up in a URL and a storage key, and "final
#: (2)+copy.png" is a different string in each of the places it is written.
UNSAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def upload_root() -> str:
    """The folder inside the configured storage that CMS uploads live under."""
    return str(getattr(settings, "CMS_UPLOAD_PATH", "cms/uploads")).strip("/")


def max_upload_bytes() -> int:
    """The largest file an editor may upload, or ``0`` for no limit."""
    return int(getattr(settings, "CMS_MAX_UPLOAD_BYTES", 0) or 0)


def safe_name(name: str) -> str:
    """A storage-safe, URL-safe version of an uploaded file's own name.

    The stem is kept -- shortened, ASCII-folded and stripped of anything that
    would need escaping -- because a folder of ``a1b2c3.png`` is unsearchable
    for the person who uploaded ``pricing-hero.png`` this morning. Uniqueness
    comes from a suffix rather than from the name, so two people uploading
    ``logo.png`` do not overwrite each other.
    """
    stem = PurePosixPath(name or "file").name
    folded = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    cleaned = UNSAFE.sub("-", folded).strip("-.") or "file"
    root, _, extension = cleaned.rpartition(".")
    root = (root or cleaned)[:60].strip("-") or "file"
    suffix = uuid.uuid4().hex[:8]
    return f"{root}-{suffix}.{extension.lower()}" if extension else f"{root}-{suffix}"


def extension_of(name: str) -> str:
    """``"Hero.PNG"`` -> ``".png"``. Empty when the name has no extension at all."""
    _, dot, extension = PurePosixPath(name or "").name.rpartition(".")
    return f".{extension.lower()}" if dot else ""


def check(upload: Any, field_type: Any) -> None:
    """Raise :class:`ValidationError` if this file may not be stored for this type."""
    limit = max_upload_bytes()
    size = getattr(upload, "size", 0) or 0
    if limit and size > limit:
        raise ValidationError(f"That file is {size // 1024} KB. The limit is {limit // 1024} KB.")
    allowed = UPLOAD_EXTENSIONS.get(field_type)
    if not allowed:
        # An empty tuple means "this type takes anything" -- which is what a
        # generic File field is for. `None` means the type takes no uploads at
        # all, and the form never offers one, so there is nothing to refuse.
        return
    extension = extension_of(getattr(upload, "name", ""))
    if extension not in allowed:
        raise ValidationError(
            f"{extension or 'That file'} is not one this field takes. "
            f"Allowed: {', '.join(allowed)}."
        )


def store(upload: Any, field_type: Any) -> str:
    """Save one uploaded file and return the URL it is now served under.

    Grouped by type -- ``cms/uploads/image/…`` -- so that a bucket's lifecycle
    rules, a CDN's cache headers and a person looking for last week's video all
    have something to work with other than one enormous folder.
    """
    check(upload, field_type)
    key = posixpath.join(upload_root(), str(field_type), safe_name(getattr(upload, "name", "")))
    stored = default_storage.save(key, upload)
    return default_storage.url(stored)
