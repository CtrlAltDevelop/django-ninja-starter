"""Notes, version 1: full CRUD over one account's own rows.

``auth=api_auth`` is doing two jobs. It refuses anonymous callers, and it puts
the resolved account on ``request.user`` -- which is what every query below
filters on. Ownership is enforced in the queryset rather than checked after the
fetch, so a note belonging to somebody else is a 404 and not a 403: an API that
distinguishes the two tells a stranger which ids exist.
"""

import uuid

from django.http import HttpRequest
from ninja import Router, Status

from apps.notes.models import Note
from apps.notes.schemas import MessageOut, NoteIn, NoteOut, NotePatch
from infrastructure.auth.core.sessions import api_auth
from infrastructure.common.errors import ApiError

router = Router()


def _owned(request: HttpRequest, note_id: uuid.UUID) -> Note:
    note = Note.objects.filter(pk=note_id, owner=request.user).first()
    if note is None:
        raise ApiError("No such note.", status=404)
    return note


@router.get("/", response=list[NoteOut], auth=api_auth, summary="List your notes")
def list_notes(request: HttpRequest) -> list[Note]:
    """Every note this account owns, pinned ones first."""
    return list(Note.objects.filter(owner=request.user))


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
    return _owned(request, note_id)


@router.patch(
    "/{note_id}",
    response={200: NoteOut, 404: MessageOut},
    auth=api_auth,
    summary="Amend a note",
)
def update_note(request: HttpRequest, note_id: uuid.UUID, payload: NotePatch) -> Note:
    """Apply only the fields the client actually sent."""
    note = _owned(request, note_id)
    changed = payload.dict(exclude_unset=True, exclude_none=True)
    for field, value in changed.items():
        setattr(note, field, value)
    if changed:
        note.save(update_fields=[*changed, "updated_at"])
    return note


@router.delete(
    "/{note_id}",
    response={200: MessageOut, 404: MessageOut},
    auth=api_auth,
    summary="Delete a note",
)
def delete_note(request: HttpRequest, note_id: uuid.UUID) -> MessageOut:
    _owned(request, note_id).delete()
    return MessageOut(detail="Note deleted.")
