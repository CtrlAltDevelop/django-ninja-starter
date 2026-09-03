"""Notes, version 2: the same rows, a lighter list, and a way to search them.

Both versions are mounted at once, from the same models *and the same service*,
because the registry maps a version to a router rather than to an application.
v1 keeps answering exactly as it always did while v2 is written, which is the
whole point of versioning the router instead of editing it.
"""

import uuid

from django.http import HttpRequest
from ninja import Router, Status

from apps.notes.models import Note
from apps.notes.rest.schemas import MessageOut, NoteIn, NoteOut, NoteSummary
from apps.notes.services import note_service
from infrastructure.auth.core.sessions import api_auth

router = Router()


@router.get("/", response=list[NoteSummary], auth=api_auth, summary="List and search notes")
def list_notes(
    request: HttpRequest,
    q: str = "",
    pinned: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Note]:
    """Excerpts rather than whole bodies, filtered and paged."""
    return note_service.search(request.user, query=q, pinned=pinned, limit=limit, offset=offset)


@router.post("/", response={201: NoteOut}, auth=api_auth, summary="Write a note")
def create_note(request: HttpRequest, payload: NoteIn) -> Status[NoteOut]:
    """201, because a POST that creates a row should say which status it means."""
    note = note_service.create(request.user, **payload.dict())
    return Status(201, NoteOut.from_orm(note))


@router.get(
    "/{note_id}",
    response={200: NoteOut, 404: MessageOut},
    auth=api_auth,
    summary="Read one note",
)
def read_note(request: HttpRequest, note_id: uuid.UUID) -> Note:
    """The detail view still carries the whole body -- that is what it is for."""
    return note_service.read(request.user, note_id)
