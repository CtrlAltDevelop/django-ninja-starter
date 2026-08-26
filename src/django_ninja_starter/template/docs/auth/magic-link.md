# Magic-link login

Sign-up and sign-in by following a single-use link. Nothing to type and nothing
to remember.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/magic-link/login/start` | None | Email a sign-in link |
| `POST` | `/api/v1/auth/magic-link/logout` | None | Sign out |
| `POST` | `/api/v1/auth/magic-link/signup/start` | None | Email a sign-up link |
| `POST` | `/api/v1/auth/magic-link/verify` | None | Sign in with a link token |
<!-- /generated:routes -->

## Models

<!-- generated:models -->
_This app defines no models of its own._
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
_This app registers nothing in the admin._
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_AUTH_MAGIC_LINK_BASE_URL` | **Yes** | the page that reads the token out of the URL and posts it back. |
| `DJANGO_AUTH_EMAIL_BACKEND` | **Yes** | how the link reaches the address. |
| `DJANGO_AUTH_EMAIL_FROM` | **Yes** | the sender address recipients will see. |
<!-- /generated:settings -->

```bash
DJANGO_AUTH_METHODS=magic_link
DJANGO_AUTH_MAGIC_LINK_BASE_URL=https://yourdomain.example/auth/link
DJANGO_AUTH_EMAIL_FROM=sign-in@yourdomain.example
```

`DJANGO_AUTH_MAGIC_LINK_BASE_URL` is a hard requirement, not a nicety: without it
the emailed link points nowhere and no login can complete. The system check
refuses to let the app start quietly without it.

The URL may already carry a query string; the token is appended correctly either
way.

## Usage

### Send a link

```bash
curl -X POST .../auth/magic-link/login/start -d '{"email": "zoe@example.com"}'
```

```json
{"detail": "Check your inbox for the link.", "destination": "z***@example.com", "expires_in": 300}
```

Note what is *not* in that response: the token. Unlike the code flows, the
caller who asks for a link is given nothing to redeem -- the token exists only in
the message. That is what makes the link a proof of mailbox access rather than a
handle the requester already holds.

### Redeem it

Your landing page reads the token out of the URL and posts it back:

```bash
curl -X POST .../auth/magic-link/verify -d '{"token": "V1dK..."}'
```

Returns credentials, or a `login_ticket` if a second factor is enrolled.

### Sign up

`/signup/start` sends the same kind of link and records that sign-up asked for
it. Redeeming is a single call to `/verify` either way -- there is no separate
sign-up verify endpoint, because probing one purpose and then the other would not
work: presenting a ticket under the wrong purpose deliberately destroys it.

## Notes

The token is 256 bits of entropy and is the whole credential, so it is stored
only as a digest and is consumed on first use. A link that has been followed once
cannot be followed again, which limits what a forwarded or logged URL is worth.

Links are rate-limited per address by the shared
[core throttles](core.md#rate-limits).

Because the token travels in a URL, it is exposed to anything that logs URLs --
proxies, browser history, referrer headers on the landing page. Keep the TTL
short and make the landing page post the token rather than navigating onward with
it in the query string.
