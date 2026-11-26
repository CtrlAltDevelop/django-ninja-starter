"""Turning a file somebody sent into a row a message can claim.

Uploads go through Django's configured storage, so a project that has pointed
``STORAGES["default"]`` at S3, GCS or anything else gets that without this app
knowing. What is stored on the row is ``storage.url(...)`` -- the address that
storage serves the file under, a local path in development and a bucket URL in
production, and neither of them this module's business.

Three refusals live here, all for the same reason: the alternative is a bad file
already written to storage before anybody notices.

* **an extension the desk does not accept**, checked against
  :data:`ALLOWED_EXTENSIONS`. The browser's reported MIME type is the client's
  claim; the extension is at least the name the file will be served under, and
  the one a static host will pick a ``Content-Type`` from. The default list is
  documents and images, and deliberately excludes anything a browser would
  execute -- a support desk is a place strangers send you files, which is the
  worst possible place to be relaxed about that.

  ``.svg`` is not on it, which surprises people, because an SVG is a document
  rather than a picture: it may carry ``<script>``, and a browser opening one
  served from this deployment's own origin runs it there. An agent clicking an
  attachment a stranger sent is exactly the click that turns that into a session.
  A desk that needs vector art and serves its uploads from a separate origin can
  put it back through ``DJANGO_SUPPORT_UPLOAD_EXTENSIONS``.

  ``.xml`` is left off for the same reason: an XML file with an XHTML-namespace
  ``<script>`` runs just as an SVG does.
* **a file over ``SUPPORT_MAX_UPLOAD_BYTES``**, checked before the read rather
  than after, so an oversized upload costs a rejection and not a disk.
* **too many staged uploads at once**, so a client cannot use the staging area
  as free storage by uploading and never attaching. What is left unattached is
  swept by ``manage.py support_prune``.
"""

import posixpath
from pathlib import PurePosixPath
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.utils import timezone

from apps.support.models import Upload
from infrastructure.common.uploads import check_size, extension_of, safe_name

__all__ = ["check", "extension_of", "safe_name", "store"]

#: What a client may send the desk. A allowlist rather than a blocklist: the
#: interesting extensions are the ones nobody thought of, and a blocklist is a
#: list of the ones somebody did.
ALLOWED_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".pdf",
    ".txt",
    ".log",
    ".csv",
    ".json",
    ".md",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".gz",
    ".tar",
    ".mp4",
    ".mov",
    ".webm",
    ".mp3",
    ".wav",
    ".heic",
)

#: How many unattached uploads one account may hold at a time.
MAX_STAGED = 20


def upload_root() -> str:
    """The folder inside the configured storage that support uploads live under."""
    return str(getattr(settings, "SUPPORT_UPLOAD_PATH", "support/uploads")).strip("/")


def max_upload_bytes() -> int:
    """The largest file anybody may send, or ``0`` for no limit."""
    return int(getattr(settings, "SUPPORT_MAX_UPLOAD_BYTES", 0) or 0)


def allowed_extensions() -> tuple[str, ...]:
    """What the project accepts. Overridable, because one desk's junk is another's job.

    A shop's support desk wants ``.csv``; a design agency's wants ``.psd``; a
    project that has put an antivirus in front of its storage may reasonably
    want everything. An empty setting means no extension check at all, which is
    a choice a deployment is allowed to make out loud.
    """
    configured = getattr(settings, "SUPPORT_UPLOAD_EXTENSIONS", None)
    if configured is None:
        return ALLOWED_EXTENSIONS
    return tuple(str(item).lower() for item in configured)


def check(user: Any, upload: Any) -> None:
    """Raise :class:`ValidationError` if this file may not be stored by this account."""
    check_size(getattr(upload, "size", 0) or 0, max_upload_bytes())
    allowed = allowed_extensions()
    if allowed:
        # The name it will be stored under, which `safe_name` folds and trims.
        extension = extension_of(safe_name(getattr(upload, "name", "")))
        if extension not in allowed:
            raise ValidationError(
                f"{extension or 'That file'} is not a kind the desk accepts. "
                f"Allowed: {', '.join(allowed)}."
            )
    staged = Upload.objects.filter(owner=user, attachment__isnull=True).count()
    if staged >= MAX_STAGED:
        raise ValidationError(
            f"You have {staged} files uploaded and not yet sent. Send them, or wait."
        )


def store(user: Any, upload: Any) -> Upload:
    """Save one file and return the staged row a message can claim it by.

    Grouped by day -- ``support/uploads/2026/09/08/…`` -- so that a bucket's
    lifecycle rules, a CDN's cache headers and a person looking for last week's
    attachment all have something to work with other than one enormous folder.
    """
    check(user, upload)
    today = timezone.now()
    key = posixpath.join(
        upload_root(),
        f"{today:%Y/%m/%d}",
        safe_name(getattr(upload, "name", "")),
    )
    stored = default_storage.save(key, upload)
    return Upload.objects.create(
        owner=user,
        name=PurePosixPath(getattr(upload, "name", "") or "file").name[:255],
        url=default_storage.url(stored),
        content_type=str(getattr(upload, "content_type", "") or "")[:120],
        size=getattr(upload, "size", 0) or 0,
    )
