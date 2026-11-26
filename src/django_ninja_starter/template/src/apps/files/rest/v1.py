"""Uploading, listing, downloading and deleting files over HTTP.

Every decision is :mod:`apps.files.services`'s. A file belonging to somebody
else answers 404, exactly as a missing one does, unless the caller is staff.
"""

from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.http import FileResponse, HttpRequest
from ninja import File, Router
from ninja.errors import HttpError
from ninja.files import UploadedFile

from apps.files import services
from apps.files.rest.schemas import DeletedOut, FileOut, FilePage

try:  # pragma: no cover - exercised by whichever branch the project installs
    from infrastructure.auth.core.sessions import api_auth
except ImportError:  # pragma: no cover - only in a project without the auth apps
    from ninja.security import django_auth as api_auth  # type: ignore[assignment]

from infrastructure.common.throttling import upload_throttles

router = Router(auth=api_auth)


def _found(method: Any, *args: Any) -> Any:
    try:
        return method(*args)
    except services.FileNotFound as missing:
        raise HttpError(404, str(missing)) from None


@router.post(
    "",
    response={201: FileOut},
    throttle=upload_throttles(),
    summary="Upload a file",
)
def upload_file(
    request: HttpRequest,
    # `File(...)` in the default is how django-ninja declares a multipart body.
    file: UploadedFile = File(...),  # noqa: B008
) -> tuple[int, dict[str, Any]]:
    """Store one file owned by the caller. The content is sniffed, not trusted."""
    try:
        return 201, services.upload(request.user, file.name or "", file, file.size or 0)
    except ValidationError as refused:
        raise HttpError(400, "; ".join(refused.messages)) from None


@router.get("", response=FilePage, summary="List files this account may see")
def list_files(
    request: HttpRequest, limit: int = services.DEFAULT_PAGE, offset: int = 0
) -> dict[str, Any]:
    """The caller's own files, newest first; every file for staff."""
    return services.list_files(request.user, limit=limit, offset=offset)


@router.get("/{file_id}", response=FileOut, summary="Read one file's metadata")
def get_file(request: HttpRequest, file_id: UUID) -> dict[str, Any]:
    return _found(services.get, request.user, file_id)


@router.get("/{file_id}/download", summary="Download one file's bytes")
def download_file(request: HttpRequest, file_id: UUID) -> FileResponse:
    """Always an attachment, with the sniffed type, so nothing renders inline."""
    meta, handle = _found(services.open_file, request.user, file_id)
    return FileResponse(
        handle, as_attachment=True, filename=meta["name"], content_type=meta["content_type"]
    )


@router.delete("/{file_id}", response=DeletedOut, summary="Delete one file")
def delete_file(request: HttpRequest, file_id: UUID) -> dict[str, Any]:
    _found(services.delete, request.user, file_id)
    return {"id": file_id, "deleted": True}
