# Documentation

One page per app. Each covers the same five things: its **routes**, its
**models**, its **admin**, its **setup**, and how to **use** it.

The routes, models, admin and setup tables are generated from the apps
themselves — see [keeping these pages honest](#keeping-these-pages-honest).

## Start here

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

## Shared apps

Installed automatically, no routes of their own.

- [`auth_core`](auth/core.md) — shared identity records, the challenge store,
  delivery backends, rate limits, and the audit trail.
- [`oauth_core`](oauth/core.md) — clients, scopes, consents, social accounts, and
  the signing layer.

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
