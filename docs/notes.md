# Notes (the example feature app)

Not part of the starter: a worked example of what you add to it. It lives in
[`examples/notes/`](../examples/notes) and is installed only in the project
[`examples/build.py`](../examples/build.py) generates, where `startapi`
registers it at **two API versions** over one set of models.

It exists to answer the question a starter usually leaves hanging — *what does a
real feature app look like here?* — with something the test suite runs rather
than something a README describes.

## What it demonstrates

**One model, two versions, no duplication.** `v1` lists whole notes; `v2` lists a
lighter summary and can search. Both read the same table. A list that carries
every note's full body is fine with ten rows and ruinous with ten thousand, and
trimming it is a breaking change — which is the reason to have a `v2` at all,
and the reason the starter makes adding one a single command.

**Ownership as a database concern.** Every query filters on the caller and the
index is built for that filter, rather than fetching a row and checking who owns
it afterwards.

**404 rather than 403 for somebody else's row.** A 403 tells a stranger which
ids exist. Another account's note reads exactly like a note that was deleted.

**`settings.AUTH_USER_MODEL`, never the model class.** A direct import nails the
table to whichever user model happened to be active when the migration ran.

## Routes

Both versions mount at `/notes`, so the version in the path is the only
difference a client sees.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/notes/` | Bearer | List your notes, pinned first |
| `POST` | `/api/v1/notes/` | Bearer | Write a note |
| `GET` | `/api/v1/notes/{note_id}` | Bearer | Read one note |
| `PATCH` | `/api/v1/notes/{note_id}` | Bearer | Amend a note |
| `DELETE` | `/api/v1/notes/{note_id}` | Bearer | Delete a note |
| `GET` | `/api/v2/notes/` | Bearer | List summaries, with `?q=` to search |
| `POST` | `/api/v2/notes/` | Bearer | Write a note |
| `GET` | `/api/v2/notes/{note_id}` | Bearer | Read one note |

This table is written by hand rather than generated, because the app is not
installed in *this* project — it is installed in the example one, which is where
its tests run.

## Building it yourself

```bash
make example          # generate the project, install notes, tour every app
```

Or, in any project made from this starter:

```bash
python manage.py startapi notes --api-version v1
python manage.py startapi notes --api-version v2
```

`startapi` writes the app, an endpoint, a test and the registry entry; the
example then copies real models, an admin, two routers and their tests over the
scaffolding. See [`common`](common.md#adding-a-versioned-api) for what the
command does and where the registry lives.

## Using it as a template

Copy `examples/notes/` into `src/apps/`, register it with `startapi`, and change
the model. What is worth keeping is the shape: schemas that differ per version,
one queryset that enforces ownership, and tests that cover both versions of
every endpoint.
