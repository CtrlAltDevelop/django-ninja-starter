# Documentation

One page per app — every app this project ships, whether it is always installed,
optional, or an example you copy. Each covers the same five things: its
**routes**, its **models**, its **admin**, its **setup**, and how to **use** it.

The routes, models, admin and setup tables are generated from the apps
themselves — see [keeping these pages honest](#keeping-these-pages-honest).

| | |
| --- | --- |
| Always installed | [`common`](common.md), [`accounts`](accounts.md) |
| Login methods | [`auth_password`](auth/password.md), [`auth_email_code`](auth/email-code.md), [`auth_sms_code`](auth/sms-code.md), [`auth_magic_link`](auth/magic-link.md) |
| Second factors | [`auth_twofactor`](auth/twofactor.md) |
| Social providers | [`oauth_google`](oauth/google.md), [`oauth_apple`](oauth/apple.md), [`oauth_microsoft`](oauth/microsoft.md), [`oauth_github`](oauth/github.md) |
| Token modes | [`oauth_sliding`](oauth/sliding.md), [`oauth_session`](oauth/session.md), [`oauth_rotation`](oauth/rotation.md) |
| Shared by those | [`auth_core`](auth/core.md), [`oauth_core`](oauth/core.md) |
| Feature apps | [`cms`](cms.md), [`notifications`](notifications.md), [`shop`](shop.md), [`support`](support.md) |
| Cross-cutting | [The response envelope](responses.md), [Signing in](signing-in.md), [Credentials](credentials.md), [The admin](admin.md) |

## Start here

- **[Signing in with any one method](signing-in.md)** — the path a client walks
  whichever method you enabled, and the parts that never change. Start here if
  you are wiring a client.
- **[The response envelope](responses.md)** — the six keys every JSON body has,
  and the `title` enum a client keys its translations off. Every other page
  shows the payload that goes inside it.
- **[Accounts](accounts.md)** — the user model every login resolves to, and the
  profile attached to it. Always installed.
- **[Credentials and token modes](credentials.md)** — what every login hands back,
  and how to choose between the three token modes. Read this first; every method
  below ends by minting one of these.

## Login methods

Enable them by name in `DJANGO_AUTH_METHODS`. Nothing is installed and no routes
exist for a method that is absent.

| App | Method | What the user does |
| --- | --- | --- |
| [`auth_password`](auth/password.md) | `password` | Types a password they chose |
| [`auth_email_code`](auth/email-code.md) | `email_code` | Types a code sent to their inbox |
| [`auth_sms_code`](auth/sms-code.md) | `sms_code` | Types a code sent to their phone |
| [`auth_magic_link`](auth/magic-link.md) | `magic_link` | Follows a single-use link |

## Second factors

Enable them by name in `DJANGO_AUTH_SECOND_FACTORS`. One app covers all four:
[`auth_twofactor`](auth/twofactor.md) — `totp`, `sms`, `email`, `recovery`.

## Social sign-in

Enable them by name in `DJANGO_OAUTH_PROVIDERS`.

| App | Provider | Notes |
| --- | --- | --- |
| [`oauth_google`](oauth/google.md) | `google` | OIDC, profile from the ID token |
| [`oauth_apple`](oauth/apple.md) | `apple` | Cross-site `form_post`, HTTPS required, no PKCE |
| [`oauth_microsoft`](oauth/microsoft.md) | `microsoft` | Tenant decides who may sign in |
| [`oauth_github`](oauth/github.md) | `github` | Not OIDC, profile fetched over REST |

## Token modes

One is active at a time, chosen by `DJANGO_AUTH_TOKEN_MODE`. All three publish the
same endpoints at `/auth/token`, so clients do not change with the mode.

| App | Mode | Refresh means |
| --- | --- | --- |
| [`oauth_sliding`](oauth/sliding.md) | `sliding` | Push the idle deadline out |
| [`oauth_session`](oauth/session.md) | `session` | Mint another access token for the session |
| [`oauth_rotation`](oauth/rotation.md) | `rotation` | Spend the refresh token for a successor |

## Always installed

- [`common`](common.md) — the foundation: health endpoints, the response
  envelope, the API registry and `startapi`, settings contracts, and the admin's
  navigation and dashboard.
- [`accounts`](accounts.md) — the user model every login resolves to, and the
  profile attached to it.

## Shared apps

Installed automatically.

- [`auth_core`](auth/core.md) — shared identity records, the challenge store,
  delivery backends, rate limits, and the audit trail.
- [`oauth_core`](oauth/core.md) — clients, scopes, consents, social accounts, and
  the signing layer.

## Feature apps

Optional in the same way every login method is: naming one is what installs it.
They live in `src/apps` rather than in `src/infrastructure`, because they are
features a project chooses rather than the plumbing under them.

| App | Enabled by | What it is |
| --- | --- | --- |
| [`cms`](cms.md) | `DJANGO_CMS_ENABLED=true` | Pages, sections and typed multilingual fields, shared sections, menus, publishing with preview links, and an admin screen built for editors rather than for developers |
| [`notifications`](notifications.md) | `DJANGO_NOTIFICATIONS_ENABLED=true` | Stored notifications, a read API, and a WebSocket that pushes new ones — public before it is authenticated, private after |
| [`support`](support.md) | `DJANGO_SUPPORT_ENABLED=true` | Live chat and support tickets as one app, because a ticket is a conversation: threads, staff-only notes, attachments, SLA deadlines carried by the category, a queue the desk works, and the whole surface over REST, GraphQL, gRPC and a WebSocket |
| [`shop`](shop.md) | `DJANGO_SHOP_ENABLED=true` | A catalogue whose categories declare what their products are, several sellers per product, timed campaigns, search and merchandising lists, reviews and likes, a basket per account, and an order cycle that reserves stock and issues an invoice |

## The admin

- [**The admin**](admin.md) — themed with Unfold, with a dashboard of real
  numbers, a sidebar built from the apps that are installed, and lists that say
  more per row. Every registered model is themed, including Django's own.

## Configuring an app

Every optional app declares the settings it cannot work without, on its own
`AppConfig`. Enabling an app is what activates its requirements, so `manage.py
check` tells you what the apps *you turned on* still need, and nothing about the
rest:

```
ERRORS:
?: (auth_magic_link.AUTH_MAGIC_LINK_BASE_URL) Magic-link login needs
   AUTH_MAGIC_LINK_BASE_URL: the page that reads the token out of the URL
   HINT: Set DJANGO_AUTH_MAGIC_LINK_BASE_URL. Without it the emailed link
         points nowhere and no login can complete.
```

Message ids are the app label plus the setting, so a deployment that has made a
deliberate choice can silence exactly that one message:

```python
SILENCED_SYSTEM_CHECKS = ["auth_core.AUTH_CHALLENGE_STORE"]
```

Each page's **Setup** section is that app's declaration, rendered.

## Keeping these pages honest

A stale route table is worse than no route table: it is a promise the API does not
keep. So the reference sections are generated from the code rather than written
about it, and live between markers:

```markdown
<!-- generated:routes -->
...replaced on every run...
<!-- /generated:routes -->
```

Prose outside the markers is left alone. Regenerate after changing an endpoint,
model, admin or settings declaration:

```bash
DJANGO_SETTINGS_MODULE=config.settings.test python manage.py authdocs
```

The test settings enable every app, which is what makes the pages complete. The
test suite runs the same command with `--check` and fails if anything drifts, so
these tables cannot quietly fall behind the code.
