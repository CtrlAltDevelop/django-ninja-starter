"""The gRPC transport: bytes travel as a protobuf ``bytes`` field."""

from collections.abc import Callable
from typing import Any

import grpc
import pytest

from apps.files.grpc import files_pb2, files_pb2_grpc
from apps.files.tests.conftest import PNG, access_token

Stub = files_pb2_grpc.FileControllerStub


def test_needs_a_credential(transactional_db: None, grpc_call: Callable[..., Any]) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(Stub, "List", files_pb2.ListRequest())
    assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED


def test_round_trip(alice: Any, bob: Any, grpc_call: Callable[..., Any]) -> None:
    mine, theirs = access_token(alice), access_token(bob)
    stored = grpc_call(Stub, "Upload", files_pb2.UploadRequest(name="a.png", content=PNG), mine)
    assert stored.content_type == "image/png"

    assert grpc_call(Stub, "List", files_pb2.ListRequest(), mine).total == 1
    assert grpc_call(Stub, "Get", files_pb2.GetRequest(file_id=stored.id), mine).name == "a.png"
    download = grpc_call(Stub, "Download", files_pb2.DownloadRequest(file_id=stored.id), mine)
    assert download.content == PNG
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(Stub, "Download", files_pb2.DownloadRequest(file_id=stored.id), theirs)
    assert refusal.value.code() is grpc.StatusCode.NOT_FOUND
    deleted = grpc_call(Stub, "Delete", files_pb2.DeleteRequest(file_id=stored.id), mine)
    assert deleted.deleted


def test_a_disguised_file_is_invalid(alice: Any, grpc_call: Callable[..., Any]) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        request = files_pb2.UploadRequest(name="a.pdf", content=PNG)
        grpc_call(Stub, "Upload", request, access_token(alice))
    assert refusal.value.code() is grpc.StatusCode.INVALID_ARGUMENT
