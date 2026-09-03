"""Notes, version 1: full CRUD over one account's own rows.

``auth=api_auth`` is doing two jobs. It refuses anonymous callers, and it puts
the resolved account on ``request.user`` -- which is what the service filters
every query on. Ownership, and the decision to answer 404 rather than 403 for
somebody else's note, live in :class:`NoteService`; this file is the door.
"""

import uuid

from django.http import HttpRequest
from ninja import Router, Status

from apps.notes.models import Note
from apps.notes.rest.schemas import MessageOut, NoteIn, NoteOut, NotePatch
from apps.notes.services import note_service
from infrastructure.auth.core.sessions import api_auth

router = Router()


@router.get("/", response=list[NoteOut], auth=api_auth, summary="List your notes")
def list_notes(request: HttpRequest) -> list[Note]:
    """Every note this account owns, pinned ones first."""
    return note_service.all(request.user)


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
    return note_service.read(request.user, note_id)


@router.patch(
    "/{note_id}",
    response={200: NoteOut, 404: MessageOut},
    auth=api_auth,
    summary="Amend a note",
)
def update_note(request: HttpRequest, note_id: uuid.UUID, payload: NotePatch) -> Note:
    """``exclude_unset`` is what makes this a PATCH rather than a PUT."""
    changes = payload.dict(exclude_unset=True, exclude_none=True)
    return note_service.update(request.user, note_id, changes)


@router.delete(
    "/{note_id}",
    response={200: MessageOut, 404: MessageOut},
    auth=api_auth,
    summary="Delete a note",
)
def delete_note(request: HttpRequest, note_id: uuid.UUID) -> MessageOut:
    return MessageOut(detail=note_service.delete(request.user, note_id))
