"""The files app's contribution to the project's GraphQL schema.

GraphQL has no binary type, so bytes travel base64-encoded both ways. The size
limit is checked against the encoded length before decoding, so an oversized
upload costs a rejection rather than the memory to decode it.
"""

import base64
import binascii
from typing import Any

import strawberry
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from strawberry.types import Info

from apps.files import services
from apps.files.graph.types import FileContentType, FilePageType, FileType, file_type
from infrastructure.common.errors import ApiError
from infrastructure.common.graph.errors import require_caller, resolver
from infrastructure.common.identity import caller
from infrastructure.common.responses import ResponseTitle


def _caller(info: Info[Any, Any]) -> Any:
    return require_caller(caller(info.context.request))


def _found(method: Any, *args: Any) -> Any:
    try:
        return method(*args)
    except services.FileNotFound as error:
        raise ApiError("No such file.", status=404, title=ResponseTitle.NOT_FOUND) from error


def decode(content_base64: str) -> bytes:
    """Decode an upload, refusing one too large before spending memory on it."""
    limit = int(settings.FILES_MAX_UPLOAD_BYTES)
    if limit and len(content_base64) * 3 // 4 > limit + 2:
        raise ApiError(f"The limit is {limit // 1024} KB.", status=400)
    try:
        return base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ApiError("contentBase64 is not valid base64.", status=400) from error


@strawberry.type
class Query:
    @strawberry.field(description="Files this account may see, newest first.")
    @resolver
    def files(
        self, info: Info[Any, Any], limit: int = services.DEFAULT_PAGE, offset: int = 0
    ) -> FilePageType:
        page = services.list_files(_caller(info), limit=limit, offset=offset)
        return FilePageType(
            files=[file_type(row) for row in page["files"]],
            total=page["total"],
            limit=page["limit"],
            offset=page["offset"],
        )

    @strawberry.field(description="One file's metadata.")
    @resolver
    def file(self, info: Info[Any, Any], file_id: str) -> FileType:
        return file_type(_found(services.get, _caller(info), file_id))

    @strawberry.field(description="One file's metadata and its bytes, base64-encoded.")
    @resolver
    def file_content(self, info: Info[Any, Any], file_id: str) -> FileContentType:
        meta, content = _found(services.read, _caller(info), file_id)
        return FileContentType(
            file=file_type(meta), content_base64=base64.b64encode(content).decode()
        )


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Upload a file. The content is sniffed, not trusted.")
    @resolver
    def upload_file(self, info: Info[Any, Any], name: str, content_base64: str) -> FileType:
        user = _caller(info)
        content = decode(content_base64)
        try:
            return file_type(services.upload(user, name, ContentFile(content), len(content)))
        except ValidationError as refused:
            raise ApiError("; ".join(refused.messages), status=400) from refused

    @strawberry.mutation(description="Delete one file.")
    @resolver
    def delete_file(self, info: Info[Any, Any], file_id: str) -> bool:
        _found(services.delete, _caller(info), file_id)
        return True
