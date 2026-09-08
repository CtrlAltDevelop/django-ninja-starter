"""The notes app's contribution to the project's GraphQL schema.

There is no version prefix here, and there does not need to be: GraphQL is
versioned by deprecating fields rather than by forking the document, so what the
two REST versions express as `/api/v1` and `/api/v2` is expressed here as two
fields over one service.

Field names share one namespace with every other installed app, which is why
they are prefixed with the app name.
"""

from typing import Any
from uuid import UUID

import strawberry
from strawberry.types import Info

from apps.notes.graph.types import NoteSummaryType, NoteType
from apps.notes.services import note_service
from infrastructure.common.errors import ApiError
from infrastructure.common.graph.errors import require_caller, resolver
from infrastructure.common.identity import caller
from infrastructure.common.responses import ResponseTitle


def _caller(info: Info[Any, Any]) -> Any:
    return require_caller(caller(info.context.request))


def _note_id(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as error:
        raise ApiError("No such note.", status=404, title=ResponseTitle.NOT_FOUND) from error


@strawberry.type
class Query:
    @strawberry.field(description="Every note this account owns, pinned ones first.")
    @resolver
    def notes(self, info: Info[Any, Any]) -> list[NoteType]:
        return [NoteType.from_note(note) for note in note_service.all(_caller(info))]

    @strawberry.field(description="Search this account's notes, as excerpts.")
    @resolver
    def note_search(
        self,
        info: Info[Any, Any],
        query: str = "",
        pinned: bool | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[NoteSummaryType]:
        notes = note_service.search(
            _caller(info), query=query, pinned=pinned, limit=limit, offset=offset
        )
        return [NoteSummaryType.from_note(note) for note in notes]

    @strawberry.field(description="One note, whole.")
    @resolver
    def note(self, info: Info[Any, Any], note_id: str) -> NoteType:
        return NoteType.from_note(note_service.read(_caller(info), _note_id(note_id)))


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Write a note.")
    @resolver
    def create_note(
        self, info: Info[Any, Any], title: str, body: str = "", pinned: bool = False
    ) -> NoteType:
        note = note_service.create(_caller(info), title=title, body=body, pinned=pinned)
        return NoteType.from_note(note)

    @strawberry.mutation(description="Amend a note. Anything left unset is left alone.")
    @resolver
    def update_note(
        self,
        info: Info[Any, Any],
        note_id: str,
        title: str | None = strawberry.UNSET,
        body: str | None = strawberry.UNSET,
        pinned: bool | None = strawberry.UNSET,
    ) -> NoteType:
        changes = {
            name: value
            for name, value in (("title", title), ("body", body), ("pinned", pinned))
            if value is not strawberry.UNSET and value is not None
        }
        note = note_service.update(_caller(info), _note_id(note_id), changes)
        return NoteType.from_note(note)

    @strawberry.mutation(description="Delete a note.")
    @resolver
    def delete_note(self, info: Info[Any, Any], note_id: str) -> str:
        return note_service.delete(_caller(info), _note_id(note_id))
