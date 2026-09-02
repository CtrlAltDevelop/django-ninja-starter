"""What the notes endpoints accept and return.

The two output shapes are the reason this app has a v2 at all: a list that
carries every note's full body is the kind of response that is fine with ten
rows and ruinous with ten thousand, and trimming it is a breaking change. So v1
keeps its promise and v2 makes a new one.
"""

import uuid
from datetime import datetime

from ninja import Schema

EXCERPT_LENGTH = 140


class NoteIn(Schema):
    """A whole note. Anything omitted takes its default."""

    title: str
    body: str = ""
    pinned: bool = False


class NotePatch(Schema):
    """A partial update: ``None`` means "not supplied", not "clear it"."""

    title: str | None = None
    body: str | None = None
    pinned: bool | None = None


class NoteOut(Schema):
    id: uuid.UUID
    title: str
    body: str
    pinned: bool
    created_at: datetime
    updated_at: datetime


class NoteSummary(Schema):
    """The v2 list shape: enough to render a row, without the whole body."""

    id: uuid.UUID
    title: str
    excerpt: str
    pinned: bool
    updated_at: datetime

    @staticmethod
    def resolve_excerpt(obj: object) -> str:
        body = str(getattr(obj, "body", ""))
        return body if len(body) <= EXCERPT_LENGTH else f"{body[:EXCERPT_LENGTH].rstrip()}…"


class MessageOut(Schema):
    detail: str
