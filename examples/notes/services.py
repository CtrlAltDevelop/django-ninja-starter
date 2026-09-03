"""Everything the notes app decides, independent of who asked.

This is the file worth copying into a feature app of your own. The three
transport packages beside it -- ``rest``, ``graph``, ``grpc`` -- render what
these methods return and decide nothing themselves, which is why a note's
ownership rule is written once here rather than three times over there.

**Ownership is enforced in the queryset, not checked after the fetch.** A note
belonging to somebody else is "not found" rather than "forbidden": an API that
distinguishes the two tells a stranger which ids exist.
"""

import uuid
from typing import Any

from django.db.models import Q, QuerySet

from apps.notes.models import Note
from infrastructure.common.errors import ApiError
from infrastructure.common.responses import ResponseTitle

MAX_PAGE_SIZE = 100
EXCERPT_LENGTH = 140


class NoteService:
    """Read and write the notes one account owns."""

    def owned(self, user: Any) -> QuerySet[Note]:
        """Every query starts here, and no argument can widen it."""
        return Note.objects.filter(owner=user)

    def all(self, user: Any) -> list[Note]:
        """Every note this account owns, pinned ones first."""
        return list(self.owned(user))

    def search(
        self,
        user: Any,
        *,
        query: str = "",
        pinned: bool | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[Note]:
        """The v2 list: filtered and paged.

        ``limit`` is clamped instead of validated: a client asking for everything
        gets the maximum page, not an error it has to learn about first.
        """
        notes = self.owned(user)
        if query:
            notes = notes.filter(Q(title__icontains=query) | Q(body__icontains=query))
        if pinned is not None:
            notes = notes.filter(pinned=pinned)
        window = max(0, offset)
        return list(notes[window : window + min(max(1, limit), MAX_PAGE_SIZE)])

    def read(self, user: Any, note_id: uuid.UUID) -> Note:
        note = self.owned(user).filter(pk=note_id).first()
        if note is None:
            raise ApiError("No such note.", status=404, title=ResponseTitle.NOT_FOUND)
        return note

    def create(self, user: Any, **fields: Any) -> Note:
        return Note.objects.create(owner=user, **fields)

    def update(self, user: Any, note_id: uuid.UUID, changes: dict[str, Any]) -> Note:
        """Apply only the fields the client actually sent."""
        note = self.read(user, note_id)
        for field, value in changes.items():
            setattr(note, field, value)
        if changes:
            note.save(update_fields=[*changes, "updated_at"])
        return note

    def delete(self, user: Any, note_id: uuid.UUID) -> str:
        self.read(user, note_id).delete()
        return "Note deleted."

    def excerpt(self, note: Note) -> str:
        """Enough of the body to render a row, without the whole of it."""
        body = note.body or ""
        return body if len(body) <= EXCERPT_LENGTH else f"{body[:EXCERPT_LENGTH].rstrip()}…"


note_service = NoteService()
