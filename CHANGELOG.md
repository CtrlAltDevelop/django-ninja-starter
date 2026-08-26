# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
