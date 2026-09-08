# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.0.0] - 2026-09-08

### Added

- **A field type now earns its own widget on the content screen.** Six types
  join the eight that were there -- `markdown`, `select`, `phone`, `url`,
  `color`, `audio` and `page` -- and each was added only because it changes what
  an editor types into: a choice is a dropdown over that field's own `options`, a
  colour opens a colour picker, a phone box opens a handset's keypad, a page
  reference is a dropdown of this site's pages, and Markdown and HTML get tall
  monospaced boxes rather than a one-line input. A type that would render like
  `text` and validate like `text` is `text` with a different label, and was not
  added.
- **A media field takes an upload as readily as an address.** `image`, `video`,
  `audio` and `file` now show an upload button beside the URL box, and a list of
  them shows a multi-file picker plus one address per line -- so building a
  gallery is one file dialogue rather than nine paste operations. Uploads go
  through Django's configured `STORAGES["default"]`, so a project on S3 gets S3
  without the CMS knowing; a file whose extension the type does not take, or
  which is over `DJANGO_CMS_MAX_UPLOAD_MB`, is refused before it reaches storage.
  The screen also shows what a media field points at now -- a thumbnail for a
  picture -- because an address in a box is not a picture and replacing pictures
  is what the screen is for.
- **`Field.options`**, the choices a `select` field is limited to. Written as
  `["small", "large"]` or as `[{"value": "sm", "label": "Small"}]` for when the
  word shown and the word stored should differ. Choices on a type that has none
  are refused rather than silently dropped: an editor who typed them onto a text
  field meant to make it a dropdown.
- **Open Graph, per page and per site.** `og_title`, `og_description`, `og_image`
  and `og_url` decide what a link becomes in a chat window, a timeline or a
  search card. They are resolved on the server -- the page's own social copy,
  then its plain meta copy, then the site's -- so four frontends do not each get
  the fallback chain slightly wrong, and `og_url` is what stops three addresses
  for one page being counted as three pages.
- **Uploaded-file settings.** `DJANGO_CMS_UPLOAD_PATH`, `DJANGO_CMS_MAX_UPLOAD_MB`,
  and the `DJANGO_MEDIA_URL` / `DJANGO_MEDIA_ROOT` pair Django serves them under
  while `DEBUG` is on.
- **The example tours the shop and every admin screen.** `examples/env.example`
  turns the third feature app on, so the built project now carries all three, and
  the walkthrough shops in it end to end: a category tree with attributes, two
  sellers competing for the buy box, a basket, an atomic checkout, an invoice and
  a settled payment. A final section signs in as a superuser and opens every
  model any installed app registered -- 56 of them across 16 app labels with this
  `.env`, walked from Django's own registry rather than from a list in the tour,
  so an app added tomorrow is covered without editing it and one that ships a
  broken changelist fails the run. The CMS page the tour builds now carries one
  field of each family, and the admin section asserts the content screen really
  renders the six widgets those types should have produced.


- **A notification is dismissable, and a read can be undone.** `NotificationReceipt`
  now holds two nullable timestamps -- `read_at` and `dismissed_at` -- rather
  than existing to mean "read". Dismissing is a per-account receipt and never a
  delete, because a broadcast belongs to everybody and one person clearing an
  announcement must not remove it from anyone else's tray; it marks the row read
  on the way out, so the badge cannot claim something the tray no longer shows.
  Undo exists on both sides: `unread` puts a row back in the badge, `restore`
  puts it back in the tray and leaves read state where it was. Every change
  answers `changed`, which is false when the row was already in that state --
  still a success, since a client that fired twice should not have to care which
  arrived first.
- **The socket does everything the endpoints do.** `list`, `get` and `count`
  read the history the socket itself never pushes, and `unread_one`, `dismiss`,
  `restore` and `dismiss_all` join the commands that were already there, so a
  client holding a connection open needs no HTTP client beside it to render its
  tray. `whoami` answers "you are nobody" rather than refusing, which is the
  useful reply to a client that has just woken up, and `deauthenticate` drops
  the account while keeping the connection and the public feed -- a shared
  browser signing out should stop seeing one person's mail without losing the
  announcements. Arguments are checked rather than coerced: `{"limit": "all"}`
  is a refusal, not a quiet fall back to the default page.
- **A badge cleared on a phone clears on the laptop.** Every state change is
  published to the account's own channel as a `state` frame carrying the action,
  the ids and the new unread count, so a second device needs no polling. A
  change that changed nothing publishes nothing.
- **Filters and a total on the history.** `level`, `audience` and
  `include_dismissed` narrow the list on all four transports, and the reply
  carries the count matching the same filters, so a client can render "showing
  20 of 47" without fetching all 47.
- **`notify_users`** sends one notification each to several accounts -- one row
  per recipient, because read state is per account and a shared row would have
  to invent a roster of who it was for. Deliberately a loop rather than
  `bulk_create`, which skips `post_save` and would therefore save every
  notification and deliver none: exactly the failure the broadcast signal exists
  to prevent.
- **Retention.** `manage.py notifications_prune` deletes notifications older
  than `DJANGO_NOTIFICATIONS_RETENTION_DAYS`, receipts included by cascade, with
  `--days` and `--dry-run`. Nothing deletes anything on its own, and with no
  window configured the command refuses rather than guessing how much history to
  remove -- deleting on a timer nobody asked for is not a default worth
  shipping.
- **A `GET` for one notification**, dismissed or not, on REST, GraphQL and gRPC:
  a link to something cleared away should open it rather than read as somebody
  else's mail.

- **`DJANGO_ENV_FILE` names the env file.** A deployment can keep several side
  by side, and a process can ask for none at all with an empty value. The
  app-isolation suite needed the second option: it builds each scenario's
  environment from nothing to exercise the *shipped* defaults, and a developer's
  own `.env` was being read back in underneath it, so those tests were quietly
  reporting on whatever that machine had enabled.
- **A `shop` feature app.** Enabled with `DJANGO_SHOP_ENABLED`, and off by
  default like every other optional app. A catalogue whose categories *declare*
  what their products are -- typed, inheritable `CategoryAttribute` rows rather
  than forty nullable columns -- with brands, tags, variants, stock, images and
  SEO. Discount campaigns are rows with a window rather than a `sale_price`
  column, so a sale ends because a clock passed a timestamp; only ever the
  single best one applies, because stacking is how a shop sells at a negative
  price. On top of that: search, filters and seven named listings computed over
  the live catalogue, hand-curated collections, one review per account with a
  moderation queue, likes, and a basket priced when it is read rather than when
  it was filled. Every shopper-facing queryset starts from the caller, so
  somebody else's basket is a `404` rather than a `403`.
- **Several sellers per product.** A product row is the primary seller's offer --
  its `seller`, `price` and `stock` -- and every other seller who can fill the
  order is a `ProductOffer` with a price and stock of its own, so a
  single-vendor shop never touches the table. What a shopper is quoted is the
  cheapest live seller *after* campaigns, with a tie going to whoever dispatches
  sooner; adding to a basket without naming one takes exactly that, so the price
  in the basket is the price that was on the page. Two sellers of one thing are
  two basket lines, and each line's stock check, order line and reservation
  follow the seller it names.
- **Orders, invoices and payment.** Checkout turns a basket into an immutable
  order in one transaction: prices, tax, the seller and the delivery address are
  copied onto it and never recomputed, stock is reserved *then* rather than at
  payment so two shoppers cannot both buy the last one, an invoice is issued with
  a number of its own, and a payment is opened to be settled. Coupons, shipping
  methods with a free-from threshold, and cancellation that releases a
  reservation exactly once. No gateway is wired up: orders are created against
  the `manual` provider and settled from the admin -- "Mark as paid" turns
  reservations into sales, "Mark as rejected" records one failed attempt and
  opens another, leaving the stock reserved for the retry. The
  `payment/confirm` endpoint is the seam a real callback is pointed at, and it
  settles through the same idempotent service method the admin action calls.
- **The shop answers over all three transports.** REST, GraphQL and gRPC are
  three translations of one service, so what "bestsellers" means, whether an
  unmoderated review is visible and which seller wins are decided once.
- **ReDoc sits beside Swagger.** `/api/redoc` renders the same schema as a
  reference to read, using the ReDoc page Django Ninja already ships. It has no
  version selector of its own -- it renders the single document it is handed --
  so each registered version is its own page at `/api/<version>/redoc`, a
  version nobody registered is a `404` naming itself rather than a router's own
  miss, and the Swagger page carries a link across that follows whichever
  document its top bar is showing.


- **A notification app, opt-in through `DJANGO_NOTIFICATIONS_ENABLED`.** Stored
  notifications addressed either to one account or to everybody, a read API, an
  admin for writing one and seeing who has read it, and a WebSocket that pushes
  new ones the moment they are created. Read state is a per-account receipt
  rather than a column, because a global notification is read by each person
  separately.
- The socket is useful before it is authenticated: connecting joins the public
  channel with no credential at all, and an `authenticate` command -- or a token
  in the handshake, as `?token=`, a `bearer` subprotocol, an `Authorization`
  header or a session cookie -- adds that account's own channel to the *same*
  connection. One socket carries both audiences.
- Every command accepts that same `token`, not only `authenticate`. A client
  holding a credential can make `{"command": "unread", "token": "..."}` its
  first frame rather than spending a round trip saying who it is first: it is
  signed in exactly as `authenticate` would have signed it in, same
  `authenticated` frame and same backlog, and then the command runs. The token
  is resolved *before* the command, so a refusal never half-happens; one naming
  a different account conflicts as it would on `authenticate`; and an absent,
  null or empty one leaves the command to meet whatever answer it would have
  met alone.
- It is a plain ASGI application rather than Channels, so `config/asgi.py` now
  routes `websocket` scopes to `config/sockets.py` and everything else to Django.
  Note that `runserver` is WSGI and will never serve it; use an ASGI server.
- Fan-out is behind a broker interface with two implementations: an in-process
  one that needs nothing installed, and a Redis one for a deployment with more
  than one worker. `manage.py check` warns while the in-process default is still
  configured, rather than leaving it to be discovered under load.
- `docs/notifications.md` -- the routes, the socket protocol, the two models and
  the setup, with the generated tables kept honest by `manage.py authdocs`.

- **The Swagger page authorises itself for a staff member already signed into the
  admin.** Under every token mode but `none` the API reads `Authorization` and
  ignores cookies, so an admin session got a `401` from "Try it out" and the
  reader had to go and mint a token by hand. `POST /auth/token/from-session`
  turns that session into a real credential -- through the same
  `issue_credentials` every login uses, into the same tables, with the same
  revocation story -- and the docs page fills **Authorize** in with it on load.
  Unlike the social `/exchange` beside it, the session is *not* consumed: being
  logged out of the admin for opening the documentation would be a poor trade.
  It is staff-only, it is recorded in the audit trail as a login with the method
  `admin_session`, and `DJANGO_AUTH_SESSION_TOKEN_FOR_STAFF=false` unpublishes
  the route rather than leaving it to answer `403` -- nothing should document a
  bridge it will refuse to walk. Every refusal leaves the page as it was.
- `infrastructure/common/responses.py` -- the envelope, the `ResponseTitle` enum
  and the English gloss for each member, the renderer that wraps outgoing bodies,
  and the rewrite that makes the published OpenAPI document say so.
- `docs/responses.md` -- the response contract, every title and what it means,
  and how to raise a failure that carries one. A test fails if a title is missing
  from the page or has no description.
- `tests/test_example_project.py` -- the example project as a test app. It
  builds the project, checks that every app the `.env` names is installed, runs
  the project's own suite (the `notes` tests included), replays the walkthrough,
  and fails if the migration stops matching its model. Marked `slow`, so
  `pytest -m "not slow"` is still the fast loop.
- `make example` builds the example project from the current template and tours
  it.
- **`apps/cms` -- a content system for a site whose pages change more often than
  its code.** Pages hold sections, sections hold typed fields, and every value is
  translatable. Three public read endpoints -- `/cms/pages`, `/cms/site` and
  `/cms/pages/{page_name}` -- serve one language each, chosen from `?language=`,
  then `Accept-Language`, then `CMS_LANGUAGES[0]`, and falling back rather than
  returning holes. A field's `type` describes one item and `multiple` says how
  many, so there is no `image_list` next to `image`; values are normalised to one
  canonical shape per type on the way in, so a client never has to handle
  "sometimes a string, sometimes an object". Translations live in the row as
  `{language: value}` rather than in a column per language, so adding a language
  is a settings change.
- **A content screen built for editors rather than developers.** Structure --
  pages, sections, fields -- stays superuser-only in the ordinary Django admin,
  while *Pages -> Edit content* renders one page in one language, section by
  section, with the widget each type deserves. It asks for `cms.change_field`,
  so a site editor can be given exactly it; language tabs switch languages, and a
  second language starts empty so that saving cannot silently declare the default
  language's copy to be a translation of itself.
- `DJANGO_CMS_LANGUAGES` -- the languages content may be written in. Deliberately
  not Django's `LANGUAGES`, which names every language it ships a translation
  for.
- The app ships in the generator's template as well as in this repository, so a
  new project has it installed and registered at v1 from its first migration.
  Deleting `src/apps/cms` and its registry entry is all it takes to opt out.
- `docs/cms.md` -- the routes, the field types and their canonical values, the
  two admin screens and who each is for.
- **The admin is themed with [Unfold](https://unfoldadmin.com), everywhere.**
  Every model this project registers uses Unfold's `ModelAdmin` and inlines --
  Django's own `Group` admin included, re-registered so it is not the one
  unstyled page in a themed application. `unfold` sits ahead of
  `django.contrib.admin` in `INSTALLED_APPS`, which is the whole installation: a
  template is found in app order, and behind the admin it themes nothing. A test
  asserts that ordering.
- **A dashboard instead of a list of models.** The admin's front page leads with
  the numbers that change what somebody does next: pages, sections and fields,
  how much of each language is written, how many *required* fields are still
  empty, and this week's sign-ins with a success rate. Every block is
  permission-checked and every block is absent -- not empty -- when its app is
  not installed.
- **A sidebar built from the deployment rather than from a list.** Navigation is
  grouped into Overview, Content, People, Credentials and Audit, and assembled
  per request from the apps that actually registered, so a project running only
  password login has no Credentials group and no `NoReverseMatch` on first load.
  Every item carries a permission check: an editor with `cms.change_field` sees
  Content and nothing else.
- An environment badge on `DEBUG`, two-line list cells, coloured labels for
  status, role and field type, dropdown and radio filters, and unsaved-change
  warnings on the pages where losing an edit costs an afternoon.
- The CMS content screen is rebuilt on Unfold's layout, card component and
  widgets, so a date there opens the admin's own picker and a boolean is the
  admin's own switch.
- `docs/admin.md` -- what is different, the three callbacks that make it dynamic,
  and how to add a model or a card without it looking bolted on.
- **The CMS is opt-in, like every other app here.** `DJANGO_CMS_ENABLED=true`
  installs it, publishes its five routes on every registered API version and
  gives it an admin; unset, a project carries no CMS tables, no CMS routes and
  never imports the package. It is no longer an entry in `api_registry.json` --
  that file is for feature APIs a project scaffolds itself -- but a pair of
  settings next to the login methods, which is where a reader already looks to
  find out what is switched on. Its tests bow out of collection when it is off,
  so a project that does not want it still has a green suite.
- **The CMS became a complete, portable app rather than a sketch.** It now
  imports nothing from the project around it: refusals are Django Ninja's own
  `HttpError`, the languages setting defaults itself, and `apps/cms/theme.py`
  resolves the admin theme with Django's own behind it -- so the directory can be
  copied into any Django project, or deleted from this one, and neither leaves a
  hole. `apps/cms/README.md` is that guide.
- **Publishing.** A page is a draft until somebody publishes it, and publishing
  may carry a date, so a page can go live on its own. Drafts are 404s. A
  **preview link** shows one anyway: a signed token that names one page and
  expires, so the person who asked for the change can read it on their phone
  without an account, and one link does not open every unpublished page.
- **A library of shared sections.** A section that belongs to no page can be
  placed on many, with its own position and its own switch per page -- which is
  what a footer actually is. Copying one onto each page instead is what leaves
  five almost-identical footers behind. The API sorts a placed section in with
  the page's own and marks it `"shared": true`.
- **Menus**, because navigation is content: adding a page to the header should
  not be a deployment. An entry points at a page -- stored as the page, so
  renaming its slug moves the menu with it -- or at any other address, nested one
  level, with translated labels. Served by `/cms/menus` and `/cms/menus/{name}`.
- **`cms_export` and `cms_import`.** Content is the part of a deployment least
  likely to exist anywhere else, and a database dump is not an answer. The export
  is content keyed by slug: it seeds an environment, reads as a diff in a pull
  request, and imports back by matching slugs -- so a re-run changes nothing and a
  staging export against production edits the pages that exist. Every row is
  validated on the way in, inside one transaction, and `--prune` is a flag
  because deleting what a file omits is not what anybody wants by accident.
- The admin grew with it: publish and unpublish actions, a state label that tells
  scheduled from draft, the preview link on the page it belongs to, shared
  sections placed inline, a content screen for a library section of its own, and
  menus with their entries inline. Shared bands are badged wherever they appear,
  so nobody edits nine pages thinking they are editing one.
- **Duplicate as a draft.** The second page of a kind that already exists is the
  commonest page anybody makes; this copies its sections, its fields, every
  language of every value and its shared placements -- and never its published
  state, because the copy arrives holding text nobody has reviewed. Shared
  sections are placed again rather than copied, which is the point of them.
- A field filter for "required, still empty", the list an editor actually works
  from; **Edit content** on sections as well as pages, since a shared section has
  no page to be reached from; a clickable preview link; and the page's state,
  preview and structure links across the top of its content screen.
- A menu entry pointing at a page nobody has published is left out of the API
  rather than served as a link to a 404 -- which is what a menu written before
  its page goes live would otherwise be, and it is written first almost every
  time.
- **A documentation page for every app, and a test that says so.** `common` and
  `cms` had no page of their own -- the foundation every project carries and the
  one optional app, both undocumented -- and `notifications` had none either. All
  three now have one, with their routes, models, admin and settings tables
  generated from the code like every other page. A new test asserts the generator
  knows about every app this repository ships, so the next one cannot be
  documented by omission.
- `docs/notes.md` documents the example feature app: one model served at two API
  versions, ownership enforced in the queryset, and 404 rather than 403 for
  somebody else's row. Hand-written, because it is installed in the example
  project rather than in this one.
- The docs index opens with a table of every app by category, and the generator
  now finds mounted routers by convention -- any `*_ROUTERS` setting, plus the API
  registry -- instead of by a hard-coded list that quietly documented a new app as
  publishing no routes.
- An app that cannot import `AppSettings` without dragging the project along --
  which is any app meant to be copied elsewhere -- can declare `settings_docs` on
  its `AppConfig` instead: plain rows that document its settings without
  validating them. The CMS uses it.
- The walkthrough tours it: an editor's rows written straight into the ORM, then
  the same page read back over HTTP in English and in Persian, showing a missing
  translation falling back rather than leaving a hole.

- **A sitemap, generated rather than kept.** `GET /api/v1/cms/sitemap.xml` lists
  every live page using exactly the publishing rule the read API uses, so a draft
  and a page dated for next Tuesday are absent for the same reason they are
  absent everywhere else and there is no second definition of "live" to drift. A
  hand-kept `sitemap.xml` is wrong the moment anybody publishes. What an editor
  controls is the handful of judgements a generator cannot make: whether to
  publish one at all, what address the pages hang off, how often a crawler should
  come back, and -- per page, via `in_sitemap` -- whether a live page has any
  business in a search index at all. The XML is nine tags built by hand rather
  than through Django's `sitemaps` framework, which wants a `Site` row, a
  template loader and a URLconf entry this app is meant to survive without.
- **Every page as schema.org JSON-LD.** Open Graph decides what a link looks like
  when somebody shares it; this decides what a search engine understands the page
  to *be*, which is the difference between a blue link and a result carrying a
  site name, a breadcrumb and a logo. Generated from what the CMS already holds,
  because asking an editor to keep a JSON-LD block current is asking them to
  maintain a second copy of the title, description and address in a syntax where
  a missing brace is invisible until a crawler drops the document. It is a
  `@graph` of three linked nodes -- the `WebPage` `isPartOf` the `WebSite`, which
  is `publisher` of the `Organization` -- so a page points at its site instead of
  restating it.
- **A calendar of what running the site requires.** `SiteEvent` is the other half
  of a CMS: the domain that expires, the certificate that lapses, the campaign
  that starts on the first, the price list to check before the new year. It is a
  model rather than a calendar invitation because the person who has to act on it
  is looking at this admin, not at that calendar. A reminder is a *lead time*
  rather than a second date -- "tell me two weeks before" survives the date
  moving, and these dates move constantly -- and `repeats_yearly` is there
  because most of them are annual by nature, which is otherwise a row that goes
  stale the day after it fires and is either deleted, losing the history, or left
  to nag forever.
- **An order does not stop at "paid".** `carrier`, `tracking_number`,
  `tracking_url` and `shipped_at` on the order, and an `OrderEvent` history
  beside it, so "where is it" is answered from the order rather than from an
  email somebody sent. Every step is a row with a timestamp and an optional note,
  and who took it stays the shop's business rather than the shopper's.
- **A delivery address book.** Saved addresses per account, one of them the
  default, over all three transports: list, read, add, patch, set-default and
  remove. Every queryset starts from the caller, so somebody else's address is a
  `404`. Setting a default clears the previous one in the same transaction rather
  than leaving two, and an address referenced by a placed order is never rewritten
  by an edit -- the order copied what it needed.
- **Coupons and shipping, costed against the basket that asked.**
  `POST /api/v1/shop/cart/coupon` answers what a typed-in code is worth here and
  now, or why it is worth nothing, in one shape rather than a `400` -- a checkout
  page asks on every keystroke. A coupon is a percentage or an amount with a
  minimum subtotal; a shipping method quotes both its list `price` and the `cost`
  for this basket, because "free over 50" is only meaningful beside one and a page
  printing the list price would charge a different number a screen later.
- **Which size, from whom.** A product sold in variants asks three questions at
  once and the product page now answers all three. `variant_attributes` carries,
  per axis, the values this product is actually made in -- drawn from the variants
  that exist rather than the category's whole list, but ordered by that list, so
  every product in a category prints its sizes S, M, L -- each saying whether it
  can still be pressed and which variants answer it. That is what lets a picker
  grey a sold-out size out without every storefront deriving availability
  slightly differently. Each variant carries its own `sellers`, a `seller_count`,
  the shop's own `stock` and every seller's `total_stock`: a page with only the
  first prints "out of stock" over a size three other shops are holding.
- **A shop section on the admin dashboard.** Ordered the way a shopkeeper's day
  is rather than the way the models are -- what needs doing above what is designed
  once and edited rarely, and the records nobody edits collapsed at the bottom,
  because they are read after something went wrong rather than as part of the
  work.

### Changed

- **A URL field accepts an address on this site.** `/media/cms/uploads/…` and
  `/about-us` are URLs as far as a CMS is concerned -- it has to be, since the
  first is what this app's own upload button produces. Protocol-relative `//host`
  is still refused: it looks internal and points somewhere else.
- **A phone number written the way a country writes it is accepted.**
  `(020) 7946 0958` was refused before, which would have pushed the editor into a
  text field where the type says nothing at all.


- **Every JSON response now arrives in one envelope.** A body used to be the
  payload at the top level on success and `{"detail": "..."}` on failure, which
  is two shapes to parse and a third -- Django Ninja's raw pydantic list -- for a
  body that did not validate. All three are now
  `{errors, data, isSuccess, statusCode, title, description}`. `title` is a
  member of a fixed enum (`INVALID_CREDENTIALS`, `CODE_EXPIRED`, `TOKEN_REUSED`,
  ...), so a client can key its own translations off it rather than showing an
  English sentence to every user; `description` is the developer-facing gloss and
  is not translated. **Breaking for clients**: what was at the top level is now
  under `data`, and an error's `detail` is now `description` and `errors[0]`.
- The wrapping happens in a renderer rather than in each endpoint, so a view
  returns its own schema as before and a feature app added with `startapi` is
  enveloped from its first request. The published OpenAPI document is rewritten
  to match, so `/api/docs` and any generated client see the envelope and get
  `ResponseTitle` as an enum.
- Django Ninja's own refusals are enveloped too: a missing credential, a `404`,
  and a body that fails validation -- whose pydantic errors are flattened into
  one sentence per field in `errors`.

- **`examples/` is now one example project, generated by the package.** It used
  to be a script that imported this checkout's `src/` and a feature app nothing
  ran. `examples/build.py` now calls the same `create_project` the
  `django-ninja-starter` command calls -- an installed copy of the package when
  there is one, so CI points it at the freshly built wheel -- copies
  `examples/env.example` in as the project's `.env`, registers the `notes`
  feature app at v1 and v2 through the project's own `startapi`, and migrates.
  `examples/walkthrough.py` tours that project in a child process rather than
  this repository, so it is the published output that has to answer.
- The tour covers the feature app and every registered API version: notes across
  v1 and v2, and both OpenAPI documents instead of only v1's.
- The example's `.env` turns both shipped feature apps on as well, so the tour
  now walks the CMS -- a page of typed fields in two languages, a shared section,
  a menu and a draft behind a signed preview link -- and the notifications app,
  over HTTP *and* over its WebSocket. The socket is driven by calling the
  project's own `config/sockets.py` with the two queues an ASGI server would
  supply, so it needs no uvicorn and no open port. That also means the tour runs
  against a shared in-memory database rather than a private `:memory:`, since the
  consumer reaches the ORM from a worker thread.


- **A one-sided settings bound reads as one.** The generated settings tables
  wrote `Range 0–None` for a requirement with a minimum and no maximum; they now
  say "0 or more".

- **Availability is one rule in one place.** Three tables carry a `stock` column
  and they are not alternatives: the product row is the primary seller's shelf, a
  variant's is that size in that colour, an offer's is somebody else's warehouse.
  `pricing.shelf`, `can_fill`, `total_stock` and `is_available` are now how every
  payload, the basket's "can this still be filled" and the checkout's stock check
  ask about them, most specific winning exactly as the price does. They had each
  written the comparison out separately and disagreed: one ignored backorders,
  another ignored whether the product was counted at all.
- **A variant product's page lists the other sellers.** It listed none. The offers
  it published were filtered to those naming no variant, and a product sold in
  variants has none -- so a shirt with three other shops holding it claimed to be
  sold by this one alone.
- **Only a choice or a colour can distinguish variants.** Free text could before.
  An option is the key a shopper picks by, so "Red" and "Red " were two variants,
  two shelves and one picker offering the same colour twice. **Breaking**: a
  category attribute that is both free text and marked as distinguishing variants
  no longer validates, and has to be changed to a choice.

### Removed

- **Meta keywords.** Nothing has ranked on that tag for well over a decade, and a
  box editors dutifully fill in that nothing reads costs them time on every page
  they write. `Page.keywords` and `SiteSettings.keywords` are gone from the
  models, from all four transports and from the export format; Open Graph is what
  replaces them.

### Fixed

- **A scaffolded API version came out of `startapi` failing the project's own
  `make check`.** Two blank lines before `router = Router()` where isort wants
  one -- it is an assignment, not a definition -- and a test assertion pre-split
  across three lines that `ruff format` collapses because it fits. A generator
  that writes code its own checks reject is a generator whose first instruction
  to a new project is "now fix what I just wrote".
- **The wheel smoke test never compiled the protos it then type-checked.** CI
  scaffolds an app and runs `make check` on it; `startapi` writes a gRPC service
  whose stubs `manage.py protos` produces, and prints exactly that. The step was
  missing, so mypy failed on an import of a module nothing had generated yet.
- **The site settings form was missing every input it existed for.** Its
  per-language boxes were added in the form's `__init__`, so they never reached
  `base_fields` -- and the admin builds its layout from `base_fields`. The page
  rendered, saved, and showed the logo and the JSON columns while the site name
  and tagline were simply absent. They are declared on a per-request subclass
  now, and grouped into fieldsets.
- **The CMS content screen's date picker never opened.** The admin's date widget
  needs the translation catalogue and `core.js`, both of which a change form
  provides and a custom page does not, so `calendar.js` threw on load and the
  field rendered as a plain box. Both are included now, and the timezone note the
  same script injects is positioned against its own field rather than floating in
  the corner of the card.
- **`/api/docs` rendered "No API definition provided" and fetched nothing.** The
  page carries a `urls` list so its top bar can switch between registered API
  versions, but that list is read by Swagger UI's *topbar*, which ships in the
  standalone preset -- a second script, and `layout: "StandaloneLayout"`. Django
  Ninja's CDN page loads neither, so nothing consumed the list and no document
  was ever requested: a 200 in the log, an empty page in the browser, and no
  error to connect the two. `infrastructure/common/docs.py` now renders a page
  that loads the preset and asks for that layout. A test asserts all three
  settings together, since the old one asserted only the list and passed
  throughout.
- **A checkout could not complete when a product carried tax.** `tax_total` and
  `total` reached their columns with more decimal places than the field holds --
  a `Decimal` division produces as many as the arithmetic implies -- and the
  write was refused rather than rounded. Money is now quantised to the cent per
  line and then summed, which is also how an invoice adds up.
- **Stock never moved.** The decrement assigned an `F()` expression and saved,
  but every write in the shop runs `full_clean`, which cannot validate an
  expression; it is one `update` statement now, which is also what makes it safe
  against a second checkout running at the same time.
- **A discount arrived blank over gRPC.** The price serialiser read flattened
  `discount_*` keys from a payload that nests them, so `is_discounted` was true
  while the campaign's name, value and end date were all empty.
- **Filtering by an attribute was a 500 on SQLite**, which is what this starter
  ships with: the JSON `contains` lookup it used is unsupported there. Values
  are now normalised the way the write side normalises them and matched exactly,
  with multi-choice membership done as a substring match on the encoded value.
- **A category's product count read as `null` rather than `0`** when it had none
  of its own, which is indistinguishable from "counts were not requested".


- **`/graphql` answered a browser with a `404` in development.** The in-browser
  editor is meant to follow `DEBUG`, and its default was written as
  `str(DEBUG)` in `base` -- where `DEBUG` is always `False`, since
  `development.py` raises it only after that line has run. So the editor was off
  in the one place it is wanted, and Strawberry answers a browser `GET` with a
  `404` when it has no editor to render, which is a confusing way to be told a
  setting did not take. `development.py` now turns it on itself, and both
  settings modules are asserted rather than reasoned about.
- `DJANGO_GRAPHQL_ENABLED`, `DJANGO_GRAPHQL_GRAPHIQL`, `DJANGO_GRPC_ENABLED` and
  `DJANGO_GRPC_PORT` were in neither `.env.example` nor the README's table.
  `DJANGO_GRAPHQL_GRAPHIQL` ships commented out on purpose: written out with a
  value, copying the example into a `.env` is what would turn the editor back
  off.

## [1.0.0] - 2026-08-26

### Added

- **The project owns its user model.** An `accounts` app ships installed and
  migrated from the first migration, because every table here points at
  `AUTH_USER_MODEL` and swapping that in later is a migration nobody wants. It
  brings a unique email, a profile the login flows fill in, and a `/users`
  router mounted ahead of the auth ones. `DJANGO_AUTH_USER_MODEL` still lets a
  project substitute its own model.
- The login paths now feed that profile what they learn. Email code and magic
  link confirm the address they just proved; OAuth fills in display name and
  avatar where the person has not already set them -- filling blanks rather
  than overwriting an answer they gave.
- `examples/` -- a walkthrough of the assembled project, and a feature app
  showing what a versioned router looks like beside it.
- Isolation tests that boot the project with one app enabled at a time, in a
  fresh interpreter, and sign somebody in. The main suite runs with everything
  on, which is the one configuration nobody deploys.
- Interoperability tests proving a credential from any method is accepted by
  every protected endpoint, that ending one session closes all of them, and that
  a second factor enrolled through one method gates the others too.
- The published project URLs are pinned to the git remote, so a fork is told to
  update its metadata rather than quietly publishing links to another repository.

### Changed

- **The token mode now defaults to issuing tokens.** Left unset,
  `DJANGO_AUTH_TOKEN_MODE` follows `DJANGO_OAUTH_MODE` when that names one, and
  is otherwise `rotation` as soon as anything signs users in. Enabling only a
  login method previously fell through to `none`, which handed back a session
  cookie and an empty `access_token` -- a working login and an unusable API. A
  project that wants Django sessions now asks for `none` explicitly. The active
  mode's app is installed for you, so a login method no longer has to be paired
  with `DJANGO_OAUTH_MODE` by hand.
- `POST /auth/password/change` answers **400**, not 401, when `current_password`
  is wrong. The caller is authenticated -- that is how they reached the endpoint
  -- and 401 would send a client that refreshes on 401 round a loop renewing a
  perfectly good token over a typo.
- A challenge subject that is not a usable primary key now reads as "no such
  account" rather than raising. Subjects come out of the challenge store as
  strings while the primary key is a UUID, and an unguarded `filter(pk=...)`
  crashes on the mismatch instead of reporting a failed lookup.
- The distribution version is single-sourced from the package, so `--version`
  and the published metadata cannot drift apart.

## [0.2.0] - 2026-08-25

### Added

- **Signed credentials.** Every login, signup, magic-link redemption and
  second-factor completion returns a JWT. The credential row's handle survives
  as the token's `jti`, so a forged or expired token is rejected without a query
  while revocation stays immediate. HS256 works from `DJANGO_SECRET_KEY` alone;
  `RS*`/`ES*` requires a real key pair and the system checks insist on it.
- **Token endpoints.** `/auth/token/refresh`, `/auth/token/revoke`,
  `GET /auth/token/sessions` and `DELETE /auth/token/sessions/{id}`, mounted at
  the same prefix whichever token mode is active. Rotation spends its refresh
  token and returns a successor, ending the family and filing a reuse event if a
  spent token comes back; session mints another access token against the same
  server-side session; sliding pushes the idle deadline out.
- **Declared settings contracts.** Each optional app states on its `AppConfig`
  the settings it cannot work without, and one system check reads them all.
  Enabling an app is what activates its requirements, so a deployment is told
  what the apps *it turned on* still need. Message ids are the app label plus the
  setting, so one nag can be silenced without silencing a family.
- **Real admin for every app.** Audit tables are read-only, credential tables
  gain a revoke action, and stored secrets are excluded from every form.
- **Per-app documentation** under `docs/`, covering routes, models, admin, setup
  and usage for each login method, second factor, provider and token mode.
- **Feature extras** in the generated project: `oauth`, `redis`, `totp` and
  `all`, so a project that only wants password login does not ship an HTTP client
  and a Redis driver it never opens.

### Changed

- The eight `oauth_*` apps now live under one `infrastructure/oauth/` package,
  mirroring how `infrastructure/auth/` is laid out. Every Django app label is
  unchanged, so no migration, model lookup or `related_name` moved with the code.
- `AuthError` is now an `ApiError` from `infrastructure/common/errors.py`, shared
  with the token endpoints so both halves of the layer render failures the same
  way.
- The `redis` driver is imported when the Redis challenge store is first opened
  rather than at module import, which is what makes it optional.
- Checks that a per-setting declaration subsumes have been removed from
  `auth/core/checks.py` and `oauth/core/checks.py`. What remains is what one
  declaration cannot answer: whether the token mode has its app, whether the SMS
  second factor has real delivery, Apple's HTTPS-only callback, Microsoft's
  tenant format, and the signing setup.

### Fixed

- A refresh-token reuse event and the family revocation that accompanies it were
  written inside the transaction that then raised to report the theft, so both
  were rolled back. Detection stays under the row lock; acting on it happens once
  the block has unwound.
- The OAuth callback binds a pending attempt to the browser that started it,
  rejects a profile carrying no subject identifier, clips provider-supplied
  fields to their column widths, clears stored tokens when a provider stops
  returning them, and follows the currently configured Microsoft tenant rather
  than the one loaded at first import.
- Two stale admin column references that would have been a 500 on a page nobody
  visits until they need it.
- The repository's own `.env.example` was missing the whole `DJANGO_AUTH_*` block
  the template had already gained.

## [0.1.0] - 2026-08-24

### Added

- Initial release: the Django Ninja starter template, the project generator CLI,
  versioned APIs from a declarative registry, and configurable OAuth providers
  with sliding, session and rotation token modes.
