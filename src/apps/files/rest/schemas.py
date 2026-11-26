"""The contract the file endpoints publish."""

from datetime import datetime
from uuid import UUID

from ninja import Schema


class FileOut(Schema):
    """One stored file's metadata. The bytes come from `/files/{id}/download`."""

    id: UUID
    owner_id: str
    name: str
    content_type: str
    size: int
    created_at: datetime


class FilePage(Schema):
    files: list[FileOut]
    total: int
    limit: int
    offset: int


class DeletedOut(Schema):
    id: UUID
    deleted: bool
