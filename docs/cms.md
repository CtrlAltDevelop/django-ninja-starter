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
| `GET` | `/api/v1/cms/sitemap.xml` | None | Every live page, as sitemap XML |
<!-- /generated:routes -->

All six are public and read-only. Five answer JSON in the usual
[envelope](responses.md); `sitemap.xml` answers `application/xml`, because it is
read by crawlers rather than by this site's frontend and has to be the document
they already know how to read. Content is written in the admin: an API that
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
  "meta": {
    "title": "Acme", "description": "…",
    "og_title": "Acme — we make things", "og_description": "…",
    "og_image": "https://…/card.png", "og_url": "https://acme.example.com/"
  },
  "json_ld": { "@context": "https://schema.org", "@graph": ["…"] },
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
a client never has to make a second request to render a `<head>`. The four `og_`
values are **already resolved**: each falls back to the page's plainer wording
and then to the site's, so a client writes them into meta tags without a
fallback chain of its own. There is no `keywords` — see
[Search and sharing](#search-and-sharing). `shared` marks
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

A type is only worth having if it changes what an editor types into. Every one
below earns its own widget on the content screen; a type that would render like
`text` and validate like `text` is `text` with a different label, and is not
added.

| Type | Canonical value | What the admin shows |
| --- | --- | --- |
| `text` | a string | A one-line box |
| `textarea` | a string | A four-row box |
| `markdown` | a string | A ten-row monospaced box |
| `html` | a string | A fourteen-row monospaced box |
| `select` | one of the field's own `options` | A dropdown of exactly those options |
| `number` | a number, never a boolean | A number input |
| `boolean` | `true` or `false` | A switch |
| `date` | `"2026-09-01"` | The admin's date picker |
| `datetime` | ISO 8601, e.g. `"2026-09-01T10:30:00Z"` | The admin's date and time picker |
| `email` | a validated address | An email input |
| `phone` | the number as written: `"+44 20 7946 0958"` | A `tel` input, so a handset opens its keypad |
| `url` | an absolute URL, or an address starting `/` | A URL input |
| `color` | `"#aabbcc"`, lower-cased | A colour picker |
| `page` | another page's slug | A dropdown of this site's pages |
| `link` | `{"url", "title", "meta"}` | A URL box and a title box |
| `image`, `video`, `audio`, `file` | `{"url", "title", "alt", "meta"}` | An **upload button**, a URL box, a title and alt text |
| `contact` | an object of strings | A JSON box |
| `json` | anything JSON can hold | A JSON box |

With `multiple` set, the same type holds a list of those. The screen follows:

| `multiple` on | What the admin shows |
| --- | --- |
| `select` | A multiple-select of the field's options |
| `image`, `video`, `audio`, `file` | A **multi-file upload**, plus one address per line |
| `link`, `url`, `page`, `email`, `phone`, `text`, `color` | One value per line |
| everything else | A JSON list box |

Values are normalised before they are stored, so a client can rely on the shape:
an editor who types a bare URL into an image field gets the full object out of
the API, not sometimes a string and sometimes an object. `meta` is the escape
hatch for anything this app has no opinion about — an image's dimensions, a
link's `rel` — and keeps the named keys small enough to be typed.

`select` is the one type whose valid values are a property of the *field* rather
than of the type, so it carries `options`:

```json
["small", "large"]
[{"value": "sm", "label": "Small"}]
```

The second form is for when the word shown and the word stored should differ —
which they should as soon as the label has to be rewritten without every page
that used it changing underneath.

## Uploading files

A media field stores an **address**, never a file. That is what lets one field
hold a picture somebody uploaded here and a picture already on a CDN, and it is
why the content screen offers an upload button *and* a URL box side by side:
they are the same answer arrived at differently, and an editor should not have
to know which one the project set up.

Uploads go through Django's configured `STORAGES["default"]`, so a project
pointed at S3, GCS or anything else gets that without the CMS knowing. What is
stored is `storage.url(...)` — a `/media/…` path locally, a bucket URL in
production.

Two things are refused before anything reaches storage:

- **an extension the type does not take.** An image field takes `.avif`, `.gif`,
  `.jpeg`, `.jpg`, `.png`, `.svg`, `.webp`; video and audio their own lists; a
  `file` field takes anything, which is what it is for. Checked by extension
  rather than by the browser's reported MIME type, because the extension is at
  least the name the file will be served under.
- **a file over `DJANGO_CMS_MAX_UPLOAD_MB`** (20 by default; `0` means no limit,
  for a deployment whose proxy already imposes one).

Names are kept readable and made unique — `Pricing Hero.PNG` becomes
`Pricing-Hero-1a2b3c4d.png` — because a folder of `a1b2c3.png` is unsearchable
for whoever uploaded it, and two people uploading `logo.png` must not overwrite
each other. Files are grouped by type: `cms/uploads/image/…`.

## Search and sharing

`title` and `description` are the meta pair; `og_title`, `og_description`,
`og_image` and `og_url` are the card a link to the page becomes in a chat
window, a timeline or a search result. Both exist per page and per site, and a
blank page value falls back to the site's.

`og_title` is separate from `title` because the good version of each is
different: a page title is read next to the site's chrome, a shared card is read
on its own. `og_url` is what stops three addresses for one page being counted as
three pages.

**There are no meta keywords.** Nothing has ranked on that tag for well over a
decade, and a box editors dutifully fill in that nothing reads costs them time
on every page they write. It was removed in
[`0002`](../src/apps/cms/migrations/0002_open_graph_and_choice_options.py) rather
than deprecated.

## The sitemap

`GET /api/v1/cms/sitemap.xml` is generated at the moment it is asked for, from
exactly the pages the read API would serve: a draft, a page dated for next
Tuesday and a page an editor excluded are all absent, and there is no second
definition of "live" to drift from the first.

What is stored is only what a generator cannot work out, on the site settings
row: whether to publish one at all, the base URL the pages hang off, and the
default change frequency and priority. Each page may override the last two, and
may set `in_sitemap` off — for a page that is live and has no business in a
search index, like a thank-you page or a single campaign's landing page.

A page carrying its own `og_url` is listed at that address. Otherwise the entry
is the base URL plus the page's slug; with no base URL filled in the entries are
paths rather than addresses, which the site settings screen says out loud rather
than leaving to be discovered by reading the XML.

Turning `sitemap_enabled` off answers the route with a 404 rather than an empty
document — an empty `<urlset>` claims the site has no pages, which is a claim,
where a missing document says only that there is nothing to read here.

## Structured data

Every page response carries `json_ld`: the same page described in schema.org
vocabulary, ready to be dropped into a `<script type="application/ld+json">`.
Open Graph decides what a shared link looks like; this decides what a search
engine understands the page to *be*, which is the difference between a plain
blue result and one carrying a site name, a breadcrumb and a logo.

It is a `@graph` of three linked nodes rather than one flat object — a `WebPage`
that `isPartOf` a `WebSite` that has an `Organization` as its `publisher` — so
two pages are understood as two pages of one site instead of two unrelated
documents. Titles and descriptions are the ones the API already resolved, so the
block cannot disagree with the `meta` beside it, and a value nobody filled in is
left out entirely: an empty string in JSON-LD asserts that the site has no name.

Nothing about it is typed by hand. Asking an editor to maintain a second copy of
every title and address, in a syntax where a missing brace is invisible until a
crawler silently drops the block, is asking for a block that is quietly wrong.

## Site events

The one part of this app that is never served to a reader. A **site event** is a
date whoever runs the site has to act on — a domain renewal, a certificate, the
campaign that starts on the first, the price list to be checked before January —
and it is here rather than in somebody's calendar because the person who has to
act on it is looking at this admin.

The reminder is a *lead time* rather than a second date: "tell me two weeks
before" survives the date moving, and the date moves constantly. An event
appears on the dashboard once its window opens and keeps appearing after the
date has gone by, marked overdue — a list that drops a date the moment it passes
is empty exactly when it matters.

`repeats_yearly` covers the ones that are annual by nature. Ticking one off
records *which* occurrence was settled, so this year's renewal being handled
does not silence next year's, and nobody has to re-enter a row every January.

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
| `options` | JSON |  |
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
| `og_title` | JSON |  |
| `og_description` | JSON |  |
| `og_image` | Char |  |
| `og_url` | Char |  |
| `in_sitemap` | Boolean |  |
| `sitemap_changefreq` | Char |  |
| `sitemap_priority` | Decimal | nullable |

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

#### `SiteEvent`

A date somebody running this site has to do something about.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `is_active` | Boolean |  |
| `name` | Char |  |
| `kind` | Char |  |
| `happens_on` | Date |  |
| `repeats_yearly` | Boolean |  |
| `remind_days_before` | PositiveSmallInteger |  |
| `notes` | Text |  |
| `url` | Char |  |
| `is_done` | Boolean |  |
| `handled_occurrence` | Date | not editable, nullable |

#### `SiteSettings`

The one row describing whatever this content belongs to.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | PositiveSmallInteger | primary key, not editable |
| `name` | JSON |  |
| `tagline` | JSON |  |
| `description` | JSON |  |
| `logo` | Char |  |
| `favicon` | Char |  |
| `og_title` | JSON |  |
| `og_description` | JSON |  |
| `og_image` | Char |  |
| `og_url` | Char |  |
| `sitemap_enabled` | Boolean |  |
| `sitemap_base_url` | Char |  |
| `sitemap_changefreq` | Char |  |
| `sitemap_priority` | Decimal |  |
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
| `Page` | Yes | `publish_now`, `unpublish`, `duplicate`, `list_in_sitemap`, `hide_from_sitemap` | `page_header`, `state`, `structure`, `translations`, `edit_content` |
| `Section` | Yes | — | `section_header`, `belongs_to`, `used_on`, `field_count`, `active`, `edit_content` |
| `SectionPlacement` | Yes | — | `page`, `section`, `order`, `is_active` |
| `SiteEvent` | Yes | `mark_handled`, `reopen` | `event`, `when`, `countdown`, `reminder`, `state` |
| `SiteSettings` | Yes | — | `__str__` |
<!-- /generated:admin -->

Three screens, for three different jobs. Both are themed by
[the project's admin theme](admin.md), and both work without it.

**Structure** — pages, sections, fields, placements and menus — is
superuser-only, and edited the ordinary Django way: a page with its sections and
its shared placements inline, a section with its fields inline. Other staff can
see it, which is how an editor works out why a field they were promised is not
on their screen.

**Content** has its own screen at *Pages → Edit content*. One page, one language
at a time, laid out section by section in the order a reader meets them, with
[the widget each type deserves](#field-types): the admin's date picker for a
date, a switch for a boolean, a dropdown over a choice field's own options, a
colour picker for a colour, an upload button beside a URL box for an image, and
a multi-file input for a gallery. A media field also shows **what it points at
now** — a thumbnail for a picture, a link for anything else — because an address
in a box is not a picture and this screen exists to replace pictures. Only the
shapes that are open by design get a JSON box. Shared sections appear in place,
badged, with a link to edit them on their own — so nobody changes nine pages
thinking they are changing one.

It is permission-driven rather than superuser-only: `cms.change_field` is what it
asks for. Language tabs across the top switch between languages, and a second
language starts **empty** rather than prefilled — prefilling would let a save
declare the English copy to be the French translation. Emptying a box removes
that translation, and readers fall back.

Site metadata has its own form, with one input per language for the copy —
grouped into copy, search, sharing, branding and the open-ended parts, rather
than one JSON box per attribute. A page's own change form groups the same way:
*Publishing*, *Search*, *Sharing (Open Graph)*, then *Sitemap* — which shows the
exact address a crawler will be given, so a missing base URL is visible on the
screen rather than in the XML.

**Site events** is the third screen, and the only one that is about running the
site rather than writing it. It lists what is coming up soonest first, with a
state column that computes handled, due, overdue and waiting rather than showing
the raw checkbox — a yearly event ticked off last year is handled *and* due
again, and the two must not look the same. Whatever is inside its reminder
window appears on the admin dashboard, overdue items first.

The rest of what the admin gives an editor:

| | |
| --- | --- |
| **Publish now** / **Move back to draft** | Actions on the page list |
| **Duplicate as a draft** | Copies structure, content and placements; never the published state |
| **Preview** | A clickable signed link, on the page and on its content screen |
| **Filled in: required, still empty** | A field filter, for the list an editor works from |
| **Edit content** | On pages *and* on sections, since a shared one has no page to be reached from |
| **Upload** | On every media field, beside the box that takes an address instead |
| State, shared and library badges | So nobody edits nine pages thinking they are editing one |
| **List in / leave out of the sitemap** | Actions on the page list, for the pages that should not be indexed |
| **Mark as handled** / **Reopen** | Actions on site events; handling one records which occurrence it settled |
| **Upcoming site events** | A dashboard card and list, from the events whose reminder window is open |

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
| `DJANGO_CMS_UPLOAD_PATH` | Optional | Where a file uploaded on the content screen is written inside `STORAGES["default"]`. Defaults to `cms/uploads`. |
| `DJANGO_CMS_MAX_UPLOAD_MB` | Optional | The largest file the content screen accepts. Defaults to 20; `0` means no limit, for a deployment whose proxy imposes one already. |
<!-- /generated:settings -->

```bash
DJANGO_CMS_ENABLED=true
DJANGO_CMS_LANGUAGES=en-us,fa
DJANGO_CMS_UPLOAD_PATH=cms/uploads
DJANGO_CMS_MAX_UPLOAD_MB=20
```

Uploads land in whatever `STORAGES["default"]` is. With Django's default file
storage that means `MEDIA_ROOT`, served under `MEDIA_URL` — both settable as
`DJANGO_MEDIA_ROOT` and `DJANGO_MEDIA_URL`, and both unused by a project that
points `STORAGES` at a bucket. Django serves `MEDIA_URL` itself only while
`DEBUG` is on; in production a web server or the object store does.

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
ready.

### A worked example

Say the hero band needs a headline, a background picture, a call-to-action
pointing at another page, and a theme colour. In *Sections → Hero → Fields*,
four rows:

| Label | Slug | Type | Multiple | Choices |
| --- | --- | --- | --- | --- |
| Headline | `headline` | `text` | — | — |
| Background | `background` | `image` | — | — |
| Gallery | `gallery` | `image` | ✓ | — |
| Goes to | `cta-page` | `page` | — | — |
| Accent | `accent` | `color` | — | — |
| Size | `size` | `select` | — | `["small", "large"]` |

Open **Edit content** and the screen is already the right shape: a one-line box,
an upload button with a URL box beside it, a multi-file picker for the gallery,
a dropdown of this site's pages, a colour picker, and a dropdown of exactly
`small` and `large`. Nobody types JSON, and nobody types a language key.

Switch the language tab and write the same page in `fa`: the boxes start
**empty**, because a prefilled form makes a blind save declare the English copy
to be the translation. What you leave empty simply falls back on read. The page list's **Duplicate as a draft** action copies a whole page —
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
