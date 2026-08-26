"""Notes, version 2: the same rows, a lighter list, and a way to search them.

Both versions are mounted at once, from the same models, because the registry
maps a version to a router rather than to an application. v1 keeps answering
exactly as it always did while v2 is written, which is the whole point of
versioning the router instead of editing it.
"""

import uuid

from django.db.models import Q
from django.http import HttpRequest
from ninja import Router, Status

from apps.notes.models import Note
from apps.notes.schemas import MessageOut, NoteIn, NoteOut, NoteSummary
from infrastructure.auth.core.sessions import api_auth
from infrastructure.common.errors import ApiError

router = Router()

MAX_PAGE_SIZE = 100


@router.get("/", response=list[NoteSummary], auth=api_auth, summary="List and search notes")
def list_notes(
    request: HttpRequest,
    q: str = "",
    pinned: bool | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Note]:
    """Excerpts rather than whole bodies, filtered and paged.

    ``limit`` is clamped instead of validated: a client asking for everything
    gets the maximum page, not an error it has to learn about first.
    """
    notes = Note.objects.filter(owner=request.user)
    if q:
        notes = notes.filter(Q(title__icontains=q) | Q(body__icontains=q))
    if pinned is not None:
        notes = notes.filter(pinned=pinned)
    window = max(0, offset)
    return list(notes[window : window + min(max(1, limit), MAX_PAGE_SIZE)])


@router.post("/", response={201: NoteOut}, auth=api_auth, summary="Write a note")
def create_note(request: HttpRequest, payload: NoteIn) -> Status[NoteOut]:
    """201, because a POST that creates a row should say which status it means."""
    note = Note.objects.create(owner=request.user, **payload.dict())
    return Status(201, NoteOut.from_orm(note))


@router.get(
    "/{note_id}",
    response={200: NoteOut, 404: MessageOut},
    auth=api_auth,
    summary="Read one note",
)
def read_note(request: HttpRequest, note_id: uuid.UUID) -> Note:
    """The detail view still carries the whole body -- that is what it is for."""
    note = Note.objects.filter(pk=note_id, owner=request.user).first()
    if note is None:
        raise ApiError("No such note.", status=404)
    return note
