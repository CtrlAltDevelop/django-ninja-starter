"""GraphQL types for stored files."""

from typing import Any

import strawberry


@strawberry.type
class FileType:
    id: str
    owner_id: str
    name: str
    content_type: str
    size: int
    created_at: str


@strawberry.type
class FilePageType:
    files: list[FileType]
    total: int
    limit: int
    offset: int


@strawberry.type
class FileContentType:
    """A file and its bytes, base64-encoded because GraphQL has no binary scalar."""

    file: FileType
    content_base64: str


def file_type(row: dict[str, Any]) -> FileType:
    return FileType(**row)
