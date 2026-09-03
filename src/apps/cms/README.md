# The `cms` app

A self-contained content app: pages made of sections made of typed,
translatable fields, plus a library of shared sections, menus, publishing with
scheduling and preview links, and an admin screen built for whoever writes the
copy.

It is a **feature app**, not part of this starter's infrastructure. Copy the
directory into any Django project, or delete it from this one; nothing else
depends on it.

## What it needs from a project

| Requirement | Why | If it is missing |
| --- | --- | --- |
| Django 5.2+ | Model and admin APIs it uses | — |
| `django-ninja` | The read API in `rest/v1.py` | Delete `rest/`; the admin still works |
| `strawberry-graphql-django` | The GraphQL half in `graph/` | Delete `graph/`; the other doors still work |
| `django-socio-grpc` | The gRPC half in `grpc/` | Delete `grpc/`; the other doors still work |
| `CMS_LANGUAGES` | The languages content may be written in | Falls back to `[LANGUAGE_CODE]` |
| `django-unfold` | The admin theme | Falls back to Django's own admin — see `theme.py` |

Nothing here imports the project around it. Refusals are raised as Django
Ninja's `HttpError`, so whatever a project does with those — this one wraps them
in an envelope — is what happens, and the app does not need to know.

## Installing it elsewhere

```python
# settings.py
INSTALLED_APPS = [..., "apps.cms"]  # or wherever you put it
CMS_LANGUAGES = ["en-us", "fa"]  # optional; defaults to LANGUAGE_CODE
CMS_PREVIEW_TTL_SECONDS = 60 * 60 * 24  # optional; how long a preview link lasts

# urls.py / your NinjaAPI
from apps.cms.rest.v1 import router as cms_router

api.add_router("/cms", cms_router, tags=["CMS"])
```

Then `manage.py migrate`.

In this project it is wired the starter's own way instead: `DJANGO_CMS_ENABLED`
puts `apps.cms.apps.CmsConfig` into `INSTALLED_APPS` and its router into
`CMS_ROUTERS`, which `config/api.py` attaches to every registered API version.
Naming the app is the whole installation; leaving it unset costs nothing.

## What is in here

| Module | Holds |
| --- | --- |
| `models.py` | Pages, sections, fields, placements, menus, site settings |
| `fields.py` | The field types and the one canonical shape each value has |
| `translations.py` | Which languages exist, and how one is chosen per request |
| `content.py` | Rows to JSON: drafts and hidden rows gone, one language chosen |
| `services.py` | Every question this app answers, decided once for all three transports |
| `rest/v1.py` | The HTTP endpoints |
| `graph/` | The same reads as GraphQL types and queries |
| `grpc/` | The same reads as gRPC actions, and the `.proto` they generate |
| `admin.py` | Structure admin, plus the content screen |
| `forms.py` | The widgets an editor types into, one per field type |
| `uploads.py` | A picked file to the address a media field stores |
| `theme.py` | Where the admin theme comes from, and what to do without one |
| `preview.py` | Signed links that show a draft |
| `duplication.py` | Copying a page with everything on it |
| `management/commands/` | `cms_export` and `cms_import` |

## Moving content between environments

```bash
python manage.py cms_export --output content.json
python manage.py cms_import content.json          # matched by slug, safe to re-run
python manage.py cms_import content.json --prune  # also delete what the file omits
```

The document carries slugs rather than ids, so it survives the trip between
databases, reads as a diff in a pull request, and seeds a new environment.

## What it deliberately does not do

* **No write API.** Content is written in the admin. An API that also writes has
  to answer "who may edit this?" on every request; this one answers "nobody,
  here".
* **No file storage of its own.** A media field holds a URL, and an upload is
  handed straight to Django's configured `STORAGES["default"]` — so whatever a
  project already uses to store and serve files stays in charge of it, and the
  same field takes an address pasted from a CDN.
* **No page routing.** A client is given a page's `id` and builds its own URLs.
