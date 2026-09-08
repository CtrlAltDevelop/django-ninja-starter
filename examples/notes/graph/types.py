"""GraphQL types for a note.

Two shapes, for the same reason the REST schemas have two: a list that carries
every note's full body is fine with ten rows and ruinous with ten thousand. The
excerpt is trimmed by the service, so it is trimmed identically here and there.
"""

import strawberry

from apps.notes.models import Note
from apps.notes.services import note_service


@strawberry.type
class NoteType:
    id: str
    title: str
    body: str
    pinned: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_note(cls, note: Note) -> "NoteType":
        return cls(
            id=str(note.pk),
            title=note.title,
            body=note.body,
            pinned=note.pinned,
            created_at=note.created_at.isoformat(),
            updated_at=note.updated_at.isoformat(),
        )


@strawberry.type
class NoteSummaryType:
    """Enough to render a row, without the whole body."""

    id: str
    title: str
    excerpt: str
    pinned: bool
    updated_at: str

    @classmethod
    def from_note(cls, note: Note) -> "NoteSummaryType":
        return cls(
            id=str(note.pk),
            title=note.title,
            excerpt=note_service.excerpt(note),
            pinned=note.pinned,
            updated_at=note.updated_at.isoformat(),
        )
