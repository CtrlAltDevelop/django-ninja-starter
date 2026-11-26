"""Uploading, listing, downloading and deleting files over gRPC.

The calls mirror the REST endpoints one for one, with the bytes carried as a
protobuf ``bytes`` field rather than a multipart body.
"""

from typing import Any

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from apps.files import services
from apps.files.grpc.serializers import StoredFile
from infrastructure.common.errors import ApiError
from infrastructure.common.grpc.errors import action, require_caller
from infrastructure.common.identity import grpc_caller
from infrastructure.common.responses import ResponseTitle

FILE_ID = [{"name": "file_id", "type": "string"}]


def _pb2() -> Any:
    from apps.files.grpc import files_pb2

    return files_pb2


def _message(row: dict[str, Any]) -> Any:
    return _pb2().StoredFile(**row)


async def _found(method: Any, *args: Any) -> Any:
    try:
        return await sync_to_async(method)(*args)
    except services.FileNotFound as error:
        raise ApiError("No such file.", status=404, title=ResponseTitle.NOT_FOUND) from error


def _store(user: Any, name: str, content: bytes) -> dict[str, Any]:
    try:
        return services.upload(user, name, ContentFile(content), len(content))
    except ValidationError as refused:
        raise ApiError("; ".join(refused.messages), status=400) from refused


class FileService(generics.GenericService):
    """The same calls the REST router publishes under `/files`."""

    @grpc_action(
        request=[{"name": "limit", "type": "int32"}, {"name": "offset", "type": "int32"}],
        request_name="ListRequest",
        response=[
            {"name": "files", "cardinality": "repeated", "type": StoredFile},
            {"name": "total", "type": "int32"},
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        response_name="FileList",
    )
    @action
    async def List(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        page = await sync_to_async(services.list_files)(
            user, limit=request.limit or services.DEFAULT_PAGE, offset=request.offset
        )
        return _pb2().FileList(
            files=[_message(row) for row in page["files"]],
            total=page["total"],
            limit=page["limit"],
            offset=page["offset"],
        )

    @grpc_action(
        request=FILE_ID, request_name="GetRequest", response=StoredFile, response_name="StoredFile"
    )
    @action
    async def Get(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return _message(await _found(services.get, user, request.file_id))

    @grpc_action(
        request=[{"name": "name", "type": "string"}, {"name": "content", "type": "bytes"}],
        request_name="UploadRequest",
        response=StoredFile,
        response_name="StoredFile",
    )
    @action
    async def Upload(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return _message(await sync_to_async(_store)(user, request.name, request.content))

    @grpc_action(
        request=FILE_ID,
        request_name="DownloadRequest",
        response=[
            {"name": "file", "type": StoredFile},
            {"name": "content", "type": "bytes"},
        ],
        response_name="FileContent",
    )
    @action
    async def Download(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        meta, content = await _found(services.read, user, request.file_id)
        return _pb2().FileContent(file=_message(meta), content=content)

    @grpc_action(
        request=FILE_ID,
        request_name="DeleteRequest",
        response=[{"name": "id", "type": "string"}, {"name": "deleted", "type": "bool"}],
        response_name="DeleteResult",
    )
    @action
    async def Delete(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        await _found(services.delete, user, request.file_id)
        return _pb2().DeleteResult(id=request.file_id, deleted=True)


GRPC_SERVICES = [FileService]
