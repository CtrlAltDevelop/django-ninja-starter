"""What the notes app publishes over gRPC.

The same service the two routers and the resolvers call, so the same ownership
rule applies: a note belonging to somebody else is ``NOT_FOUND``, never
``PERMISSION_DENIED``.

``optional`` on the update fields is what makes an unset field mean "leave it
alone" rather than "clear it" -- the distinction a PATCH has to draw somehow.
"""

from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from apps.notes.grpc.serializers import Note, NoteSummary
from apps.notes.services import note_service
from infrastructure.common.errors import ApiError
from infrastructure.common.grpc.errors import action, require_caller
from infrastructure.common.identity import grpc_caller
from infrastructure.common.responses import ResponseTitle

NOTE_RESPONSE = [
    {"name": "id", "type": "string"},
    {"name": "title", "type": "string"},
    {"name": "body", "type": "string"},
    {"name": "pinned", "type": "bool"},
    {"name": "created_at", "type": "string"},
    {"name": "updated_at", "type": "string"},
]
UPDATE_REQUEST = [
    {"name": "note_id", "type": "string"},
    {"name": "title", "cardinality": "optional", "type": "string"},
    {"name": "body", "cardinality": "optional", "type": "string"},
    {"name": "pinned", "cardinality": "optional", "type": "bool"},
]
UPDATABLE = ("title", "body", "pinned")


def _pb2() -> Any:
    from apps.notes.grpc import notes_pb2

    return notes_pb2


def _note_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise ApiError("No such note.", status=404, title=ResponseTitle.NOT_FOUND) from error


def _note(note: Any) -> Any:
    return _pb2().Note(
        id=str(note.pk),
        title=note.title,
        body=note.body,
        pinned=note.pinned,
        created_at=note.created_at.isoformat(),
        updated_at=note.updated_at.isoformat(),
    )


class NoteService(generics.GenericService):
    """Read and write the notes one account owns."""

    @grpc_action(
        request=[],
        response=[{"name": "notes", "cardinality": "repeated", "type": Note}],
        response_name="NoteList",
    )
    @action
    async def List(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        notes = await sync_to_async(note_service.all)(user)
        return _pb2().NoteList(notes=[_note(note) for note in notes])

    @grpc_action(
        request=[
            {"name": "query", "type": "string"},
            {"name": "pinned", "cardinality": "optional", "type": "bool"},
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="SearchRequest",
        response=[{"name": "notes", "cardinality": "repeated", "type": NoteSummary}],
        response_name="NoteSummaryList",
    )
    @action
    async def Search(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        notes = await sync_to_async(note_service.search)(
            user,
            query=request.query,
            pinned=request.pinned if request.HasField("pinned") else None,
            limit=request.limit or 20,
            offset=request.offset,
        )
        pb2 = _pb2()
        return pb2.NoteSummaryList(
            notes=[
                pb2.NoteSummary(
                    id=str(note.pk),
                    title=note.title,
                    excerpt=note_service.excerpt(note),
                    pinned=note.pinned,
                    updated_at=note.updated_at.isoformat(),
                )
                for note in notes
            ]
        )

    @grpc_action(
        request=[{"name": "note_id", "type": "string"}],
        request_name="ReadRequest",
        response=NOTE_RESPONSE,
        response_name="Note",
    )
    @action
    async def Read(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        note = await sync_to_async(note_service.read)(user, _note_id(request.note_id))
        return _note(note)

    @grpc_action(
        request=[
            {"name": "title", "type": "string"},
            {"name": "body", "type": "string"},
            {"name": "pinned", "type": "bool"},
        ],
        request_name="CreateRequest",
        response=NOTE_RESPONSE,
        response_name="Note",
    )
    @action
    async def Create(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        note = await sync_to_async(note_service.create)(
            user, title=request.title, body=request.body, pinned=request.pinned
        )
        return _note(note)

    @grpc_action(
        request=UPDATE_REQUEST,
        request_name="UpdateRequest",
        response=NOTE_RESPONSE,
        response_name="Note",
    )
    @action
    async def Update(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        changes = {field: getattr(request, field) for field in UPDATABLE if request.HasField(field)}
        note = await sync_to_async(note_service.update)(user, _note_id(request.note_id), changes)
        return _note(note)

    @grpc_action(
        request=[{"name": "note_id", "type": "string"}],
        request_name="DeleteRequest",
        response=[{"name": "detail", "type": "string"}],
        response_name="Message",
    )
    @action
    async def Delete(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        detail = await sync_to_async(note_service.delete)(user, _note_id(request.note_id))
        return _pb2().Message(detail=detail)


GRPC_SERVICES = [NoteService]
