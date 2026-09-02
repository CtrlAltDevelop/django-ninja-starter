# Signing in with any one method

Nine ways in, one thing out. This page is the path a client walks whichever one
you enabled, so you can pick a method, wire it once, and not rewrite the client
when you add a second.

Each method's own page has its full route list and edge cases. This one is the
part they share.

## The shape every method returns

A finished login answers with a `LoginOut`, and it is the same object whether the
user typed a password, followed a link, or came back from Google. It arrives as
the `data` of [the response envelope](responses.md), which every body on this
page is shown without:

```json
{
  "requires_second_factor": false,
  "credentials": {
    "token_type": "bearer",
    "access_token": "eyJhbGciOi...",
    "refresh_token": "eyJhbGciOi...",
    "expires_in": 3600,
    "session_id": "8c1f2b4e-..."
  },
  "login_ticket": "",
  "methods": []
}
```

A client therefore branches on one field, not on the HTTP status:

- `requires_second_factor: false` — you are done. Keep `credentials`.
- `requires_second_factor: true` — `credentials` is `null` and `login_ticket`
  holds a ticket. Finish at [`/auth/2fa/verify`](auth/twofactor.md); `methods`
  lists which factors the account has.

The ticket is **not** a credential and opens nothing.

## Pick a front door

Each of these ends in the object above. Replace the host and enable the method in
`DJANGO_AUTH_METHODS` first.

### Password — [full page](auth/password.md)

```bash
curl -X POST .../api/v1/auth/password/login \
  -H 'Content-Type: application/json' \
  -d '{"identifier": "zoe", "password": "corr3ct-horse-battery"}'
```

One call. `identifier` is your user model's `USERNAME_FIELD`, or an email address
where that field is not `email`.

### Email code — [full page](auth/email-code.md)

Two calls. The first returns a `ticket`, the code goes to the inbox:

```bash
curl -X POST .../api/v1/auth/email-code/login/start -d '{"email": "zoe@example.com"}'
curl -X POST .../api/v1/auth/email-code/login/verify \
  -d '{"ticket": "hZ3...", "code": "418239"}'
```

### SMS code — [full page](auth/sms-code.md)

The same two steps against a phone number in international format:

```bash
curl -X POST .../api/v1/auth/sms-code/login/start -d '{"phone": "+14155550101"}'
curl -X POST .../api/v1/auth/sms-code/login/verify \
  -d '{"ticket": "hZ3...", "code": "418239"}'
```

### Magic link — [full page](auth/magic-link.md)

Two calls, but the secret never passes through the caller. `start` returns no
ticket; the token exists only in the email, and your landing page reads it out of
the URL and posts it back:

```bash
curl -X POST .../api/v1/auth/magic-link/login/start -d '{"email": "zoe@example.com"}'
curl -X POST .../api/v1/auth/magic-link/verify -d '{"token": "the-token-from-the-link"}'
```

### Social sign-in — [full page](oauth/core.md)

This one is a browser flow, so it differs in shape: send the user to `/start`,
and the callback redirects them back to your app having established a **session
cookie** rather than returning JSON.

```
GET /api/v1/oauth/google/start?next=/dashboard   -> 302 to Google
GET /api/v1/oauth/google/callback?...            -> 302 to /dashboard, cookie set
```

Under any token mode but `none`, the API does not accept that cookie — it reads
`Authorization` and nothing else. So the page you land on makes one more call to
turn the session into the same credential every other method issues:

```bash
curl -X POST .../api/v1/auth/token/exchange --cookie 'sessionid=...'
```

```json
{
  "token_type": "bearer",
  "access_token": "eyJhbGciOi...",
  "refresh_token": "eyJhbGciOi...",
  "expires_in": 3600,
  "session_id": "8c1f2b4e-..."
}
```

The session is consumed by the exchange. Leaving it live would mean one sign-in
carrying two independent credentials, only one of which logout can reach. Under
`DJANGO_AUTH_TOKEN_MODE=none` there is nothing to exchange — the cookie *is* the
credential — and the endpoint answers `409`.

## Then: the part that never changes

Everything below is identical for all nine methods and all three token modes.

### Call the API

```bash
curl .../api/v1/users/me -H 'Authorization: Bearer eyJhbGciOi...'
```

### Refresh before the access token lapses

```bash
curl -X POST .../api/v1/auth/token/refresh -d '{"refresh_token": "eyJhbGciOi..."}'
```

Always the same path. What the active mode does behind it differs — the sliding
mode pushes an idle deadline out, the session mode mints another access token,
the rotation mode spends the refresh token for a successor — but the request and
the response do not. See [token modes](credentials.md).

### List and end sessions

```bash
curl .../api/v1/auth/token/sessions -H 'Authorization: Bearer eyJhbGciOi...'
curl -X DELETE .../api/v1/auth/token/sessions/8c1f2b4e-... \
  -H 'Authorization: Bearer eyJhbGciOi...'
```

Scoped to the caller, so one account cannot end another's.

### Sign out

```bash
curl -X POST .../api/v1/auth/password/logout -d '{"token": "eyJhbGciOi..."}'
```

Each method publishes its own `/logout` and they are interchangeable — they all
call the same revocation. Either half of a pair is accepted, and an absent or
already-expired token still reads as success.

## Using more than one at once

Nothing stops it, and nothing needs to be reconciled. Every method resolves to a
row in the same [`accounts.User`](accounts.md) table and mints its credential
through the same `issue_credentials`, so a user who signs up by password and
later signs in with Google is one account with one revocation story. A token does
not remember which door it came through, beyond the `amr` claim it carries for
audit.

The one thing that *is* per-method is enabling it: a method absent from
`DJANGO_AUTH_METHODS` installs no app and publishes no routes.

## What every method refuses

Worth knowing before you write client error handling, because these are the same
everywhere:

| Status | Means |
| --- | --- |
| `400` | The code, ticket or token was wrong |
| `401` | The credential is missing, forged, expired, or revoked |
| `403` | The account is disabled |
| `404` | No account, where the method is willing to say so |
| `409` | Already exists, or already set up |
| `410` | The challenge expired — start again |
| `429` | Rate limited; a `Retry-After` header says how long |

A disabled account (`is_active = False`) is refused by every path. The first-party
methods answer `403`; a social callback is a browser redirect rather than a JSON
API call, so it ends at `400` with the title `ACCOUNT_DISABLED`.

## See also

- [The response envelope](responses.md) — the wrapper around every body here
- [Credentials and token modes](credentials.md) — choosing between the three
- [Two-factor authentication](auth/twofactor.md) — finishing a pending login
- [Accounts](accounts.md) — the user and profile behind every credential
