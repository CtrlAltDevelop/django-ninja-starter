# CMS

A self-contained content app. A **page** is something a client can ask for by
name, a **section** is a named band of it, and a **field** is one typed,
translatable piece of content inside it. Around those sit the parts a content
system is incomplete without: a library of sections shared across pages, menus,
and publishing with schedules and preview links.

It is a **feature app**, not infrastructure, and it is **opt-in**: naming it in
`DJANGO_CMS_ENABLED` is what installs it. A project that does not name it carries
no CMS tables, publishes no CMS routes, shows no CMS admin and never imports the
package — the same contract every login method, second factor and provider has.

It also imports nothing from the project around it, so the directory can be
copied into another Django project or deleted from this one, and neither leaves
a hole. Its own [`README`](../src/apps/cms/README.md) is the
drop-it-in-elsewhere guide.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/cms/menus` | None | List every menu |
| `GET` | `/api/v1/cms/menus/{menu_name}` | None | Read one menu |
| `GET` | `/api/v1/cms/pages` | None | List every published page |
| `GET` | `/api/v1/cms/pages/{page_name}` | None | Read one page and everything on it |
| `GET` | `/api/v1/cms/site` | None | Read the site metadata |
<!-- /generated:routes -->

All five are public and read-only. Content is written in the admin: an API that
also writes has to answer "who may edit this?" on every request, and this one
answers "nobody, here" instead.

`page_name` is the page's slug — the `id` the list endpoint returns.

Each takes an optional `?language=`. Without it the language comes from
`Accept-Language`, and failing that from `CMS_LANGUAGES[0]`. A translation that
has not been written falls back: the requested tag, then any variant of the same
language, then the default. So a half-translated site renders rather than
showing holes.

A page response, inside the usual [envelope](responses.md):

```json
{
  "id": "home",
  "name": "Home",
  "language": "fa",
  "status": "published",
  "meta": {"title": "Acme", "description": "…", "keywords": [], "og_image": ""},
  "sections": [
    {
      "id": "hero",
      "name": "Hero",
      "shared": false,
      "fields": [
        {"id": "headline", "name": "Headline", "type": "text",
         "multiple": false, "required": true, "value": "خوش آمدید"},
        {"id": "background", "name": "Background", "type": "image",
         "multiple": false, "required": false,
         "value": {"url": "https://…/hero.jpg", "title": null, "alt": null, "meta": {}}}
      ],
      "children": []
    },
    {"id": "footer", "name": "Footer", "shared": true, "fields": [], "children": []}
  ]
}
```

`meta` is the page's own metadata with the site's used for whatever it omits, so
a client never has to make a second request to render a `<head>`. `shared` marks
a band that comes from the library rather than from this page — rendering does
not change, but an editing client can warn that changing it changes nine pages.

## Publishing

A page is a **draft** until somebody publishes it, and publishing may carry a
date:

| State | `status` | `published_at` | In the API |
| --- | --- | --- | --- |
| Draft | `draft` | — | 404 |
| Published | `published` | empty or past | Listed and readable |
| Scheduled | `published` | future | 404 until the date passes |

Nothing is published by being written, which is the safe default for the one
table a non-developer edits directly.

**Preview links** show a draft to somebody who is not signed in — usually the
person who asked for the change, reading on their phone. The link is a signed
token rather than a session: it names one page, it expires
(`CMS_PREVIEW_TTL_SECONDS`, a day by default), and a token for one page does not
open another.

```
GET /api/v1/cms/pages/about-us?preview=ImFib3V0LXVzIg:1x1P3Q:5fKeYc…
```

The admin shows the link on the page it belongs to.

## Shared sections

A section with no page is a **library** section: written once and placed on as
many pages as want it, which is what a footer or a call-to-action actually is.
Copying one onto each page instead is the thing that leaves five
almost-identical footers behind.

Placement carries its own order, so the same footer can be last on one page and
third on another, and its own switch, so it can be hidden from one page without
being deleted from the rest. In the API a placed section is just another entry
in `sections`, sorted in with the page's own and marked `"shared": true`.

## Menus

Navigation is content: somebody who can add a page can add it to the header
without a deployment. A menu is a slug and an ordered list of entries, nested
one level. An entry points at either a **page** — stored as the page, so
renaming its slug moves the menu with it rather than leaving a dead link — or at
any other address.

```json
{"id": "main", "name": "Main", "items": [
  {"label": "Home", "page": "home", "url": null, "new_tab": false, "children": []},
  {"label": "Docs", "page": null, "url": "https://…", "new_tab": true, "children": []}
]}
```

## Field types

`type` describes **one item**; `multiple` says whether the value is one of those
or a list of them. That is why there is no `image_list` — a gallery is an image
field with `multiple` set.

| Type | Canonical value |
| --- | --- |
| `text`, `textarea`, `html` | a string |
| `number` | a number, never a boolean |
| `boolean` | `true` or `false` |
| `date` | `"2026-09-01"` |
| `datetime` | ISO 8601, e.g. `"2026-09-01T10:30:00Z"` |
| `email` | a validated address |
| `link` | `{"url", "title", "meta"}` |
| `image`, `video`, `file` | `{"url", "title", "alt", "meta"}` |
| `contact` | an object of strings |

Values are normalised before they are stored, so a client can rely on the shape:
an editor who types a bare URL into an image field gets the full object out of
the API, not sometimes a string and sometimes an object. `meta` is the escape
hatch for anything this app has no opinion about — an image's dimensions, a
link's `rel` — and keeps the named keys small enough to be typed.

## Models

<!-- generated:models -->
#### `Field`

One piece of content, typed, in as many languages as have been written.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `is_active` | Boolean |  |
| `section` | ForeignKey | → `cms.Section` |
| `name` | Char |  |
| `slug` | Slug |  |
| `help_text` | Char |  |
| `field_type` | Char |  |
| `multiple` | Boolean |  |
| `required` | Boolean |  |
| `order` | PositiveInteger |  |
| `values` | JSON |  |

#### `Menu`

A named list of links: navigation is content, not routing.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `is_active` | Boolean |  |
| `name` | Char |  |
| `slug` | Slug | unique |

#### `MenuItem`

One entry in a menu: a page, or any other address.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `is_active` | Boolean |  |
| `menu` | ForeignKey | → `cms.Menu` |
| `parent` | ForeignKey | → `cms.MenuItem`, nullable |
| `label` | JSON |  |
| `page` | ForeignKey | → `cms.Page`, nullable |
| `url` | Char |  |
| `new_tab` | Boolean |  |
| `order` | PositiveInteger |  |

#### `Page`

Something a client can ask for by name.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `name` | Char |  |
| `slug` | Slug | unique |
| `order` | PositiveInteger |  |
| `status` | Char |  |
| `published_at` | DateTime | nullable |
| `title` | JSON |  |
| `description` | JSON |  |
| `keywords` | JSON |  |
| `og_image` | Char |  |

#### `Section`

A named band of one page -- or of none, which makes it reusable.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `is_active` | Boolean |  |
| `page` | ForeignKey | → `cms.Page`, nullable |
| `parent` | ForeignKey | → `cms.Section`, nullable |
| `name` | Char |  |
| `slug` | Slug |  |
| `order` | PositiveInteger |  |

#### `SectionPlacement`

One library section, put on one page, at one position.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `is_active` | Boolean |  |
| `page` | ForeignKey | → `cms.Page` |
| `section` | ForeignKey | → `cms.Section` |
| `order` | PositiveInteger |  |

#### `SiteSettings`

The one row describing whatever this content belongs to.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | PositiveSmallInteger | primary key, not editable |
| `name` | JSON |  |
| `tagline` | JSON |  |
| `description` | JSON |  |
| `keywords` | JSON |  |
| `logo` | Char |  |
| `favicon` | Char |  |
| `og_image` | Char |  |
| `contact` | JSON |  |
| `social_links` | JSON |  |
| `extra` | JSON |  |
| `updated_at` | DateTime | not editable |
<!-- /generated:models -->

Sections, fields, placements, menus and entries each carry `is_active` — the
switch an editor reaches for far more often than deletion — and hidden rows
disappear from the API while staying editable in the admin. Pages use `status`
instead: draft and published are editorial states, not a switch.

Three decisions worth knowing about:

- **Translations live in the row.** A value is `{"en-us": …, "fa": …}`, not a
  column per language, so adding a language is a settings change rather than a
  schema migration on the one table whose whole purpose is to change without
  developers.
- **Nesting stops at one level**, for sections and for menus alike. A parent on
  another page is refused too, since a read always starts from a page and walks
  down.
- **A type change never silently clears content.** Values are re-validated
  against the new type: text becoming a textarea keeps everything, and a change
  that would invalidate what is stored is refused rather than quietly deleting a
  morning's work.

`required` is a statement for the editing screen, not a database constraint —
structure is designed before anybody has written a word, so enforcing it on the
row would make declaring a required field impossible.

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `Field` | Yes | — | `field_header`, `section`, `kind`, `required`, `complete`, `active` |
| `Menu` | Yes | — | `name`, `slug`, `entry_count`, `active` |
| `MenuItem` | Yes | — | `__str__`, `menu`, `parent`, `target`, `order`, `is_active` |
| `Page` | Yes | `publish_now`, `unpublish`, `duplicate` | `page_header`, `state`, `structure`, `translations`, `edit_content` |
| `Section` | Yes | — | `section_header`, `belongs_to`, `used_on`, `field_count`, `active`, `edit_content` |
| `SectionPlacement` | Yes | — | `page`, `section`, `order`, `is_active` |
| `SiteSettings` | Yes | — | `__str__` |
<!-- /generated:admin -->

Two screens, because two different people use them. Both are themed by
[the project's admin theme](admin.md), and both work without it.

**Structure** — pages, sections, fields, placements and menus — is
superuser-only, and edited the ordinary Django way: a page with its sections and
its shared placements inline, a section with its fields inline. Other staff can
see it, which is how an editor works out why a field they were promised is not
on their screen.

**Content** has its own screen at *Pages → Edit content*. One page, one language
at a time, laid out section by section in the order a reader meets them, with
the widget each type deserves: the admin's date picker for a date, a switch for a
boolean, a URL box with alt text beside it for an image. Lists and contact
details get a JSON box, since their shape is open by design. Shared sections
appear in place, badged, with a link to edit them on their own — so nobody
changes nine pages thinking they are changing one.

It is permission-driven rather than superuser-only: `cms.change_field` is what it
asks for. Language tabs across the top switch between languages, and a second
language starts **empty** rather than prefilled — prefilling would let a save
declare the English copy to be the French translation. Emptying a box removes
that translation, and readers fall back.

Site metadata has its own form, with one input per language for the copy —
grouped into copy, search, branding and the open-ended parts, rather than one
JSON box per attribute.

The rest of what the admin gives an editor:

| | |
| --- | --- |
| **Publish now** / **Move back to draft** | Actions on the page list |
| **Duplicate as a draft** | Copies structure, content and placements; never the published state |
| **Preview** | A clickable signed link, on the page and on its content screen |
| **Filled in: required, still empty** | A field filter, for the list an editor works from |
| **Edit content** | On pages *and* on sections, since a shared one has no page to be reached from |
| State, shared and library badges | So nobody edits nine pages thinking they are editing one |

## Moving content between environments

Content is the part of a deployment least likely to exist anywhere else, and a
database dump is not an answer: it carries ids, hashes and half the auth tables,
and it cannot be reviewed in a pull request.

```bash
python manage.py cms_export --output content.json
python manage.py cms_import content.json
python manage.py cms_import content.json --prune
```

The document holds slugs rather than ids, so it travels between databases. The
import matches on those slugs and **updates** what it finds, so running it twice
does nothing the second time and running a staging export against production
edits the pages that exist rather than replacing the database. Every row goes
through the models on the way in, so a value of the wrong shape is refused —
whole, inside one transaction — with the message an editor would have seen.
`--prune` is the exception that deletes what the document omits, and it is a
flag because that is not what anybody wants by accident.

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_CMS_ENABLED` | **Yes** | Installs the app, its migrations, its routes and its admin. Unset, a project carries no CMS at all. |
| `DJANGO_CMS_LANGUAGES` | Optional | The languages content may be written in, most preferred first. Defaults to `LANGUAGE_CODE`. |
| `DJANGO_CMS_PREVIEW_TTL_SECONDS` | Optional | How long a preview link opens a draft for. Defaults to a day. |
<!-- /generated:settings -->

```bash
DJANGO_CMS_ENABLED=true
DJANGO_CMS_LANGUAGES=en-us,fa
```

Then `python manage.py migrate` — the tables arrive with the app rather than
before it. Turning it off again leaves the tables in place and stops serving
them; `python manage.py migrate cms zero` removes them for good, after an
export if the content still matters.

`CMS_LANGUAGES` is deliberately not Django's `LANGUAGES`, which lists every
language Django ships a name for: "the languages this content is written in" has
to mean the handful an editor is really expected to fill in. Removing a language
from the list does not delete anything — content written in it stays in the row
and stops being served.

## Using it

```bash
echo "DJANGO_CMS_ENABLED=true" >> .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

In the admin: fill in **Site settings**, add a **Page** with a slug like `home`,
give it sections inline, open each section to add its fields, then use **Edit
content** on the page list to write the copy and **Publish now** when it is
ready. The page list's **Duplicate as a draft** action copies a whole page —
structure, content and shared placements — for the next one of its kind. Then:

```bash
curl http://127.0.0.1:8000/api/v1/cms/pages
curl http://127.0.0.1:8000/api/v1/cms/pages/home?language=fa
curl http://127.0.0.1:8000/api/v1/cms/menus/main
```

A client renders a page by walking `sections`, matching each section's `id`
against a component it knows, and reading fields by their `id`. Because the
section list is data, adding a band to a page is content work; only a section
`id` the client has never seen needs a developer.
