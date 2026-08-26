# Email-code login

Sign-up and sign-in with a one-time code sent to an email address. No password to
choose, forget, or reuse from another site.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/email-code/login/start` | None | Send a sign-in code by email |
| `POST` | `/api/v1/auth/email-code/login/verify` | None | Sign in with an emailed code |
| `POST` | `/api/v1/auth/email-code/logout` | None | Sign out |
| `POST` | `/api/v1/auth/email-code/signup/start` | None | Send a sign-up code by email |
| `POST` | `/api/v1/auth/email-code/signup/verify` | None | Create an account with an emailed code |
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
| `DJANGO_AUTH_EMAIL_BACKEND` | **Yes** | how the code reaches the address. |
| `DJANGO_AUTH_EMAIL_FROM` | **Yes** | the sender address recipients will see. |
<!-- /generated:settings -->

```bash
DJANGO_AUTH_METHODS=email_code
DJANGO_AUTH_EMAIL_BACKEND=infrastructure.auth.core.delivery.DjangoEmailBackend
DJANGO_AUTH_EMAIL_FROM=sign-in@yourdomain.example
DJANGO_AUTH_CODE_DIGITS=6
```

## Usage

### Sign in

```bash
curl -X POST .../auth/email-code/login/start -d '{"email": "zoe@example.com"}'
```

```json
{"ticket": "hZ3...", "channel": "email", "destination": "z***@example.com", "expires_in": 300}
```

The destination comes back masked -- enough to recognise the address you just
typed, not enough to learn one you did not.

```bash
curl -X POST .../auth/email-code/login/verify \
  -d '{"ticket": "hZ3...", "code": "418239"}'
```

Returns credentials, or a `login_ticket` if a second factor is enrolled.

### Sign up

`/signup/start` and `/signup/verify`, identically shaped. The difference is at
verification: signing up refuses an address that already has an account (`409`),
and signing in creates one if `DJANGO_AUTH_AUTO_CREATE_USERS` is true.

## Notes

Both flows send a code to whatever address was supplied and hand back a ticket,
whether or not an account exists. Answering identically is the point: the ticket
is worthless without the code, so nothing is given away, and the question "does
this address have an account" is only ever settled once the caller has proved
they can read its mail.

A code is good for `DJANGO_AUTH_CHALLENGE_TTL_SECONDS` and for
`DJANGO_AUTH_CHALLENGE_MAX_ATTEMPTS` wrong guesses, after which the challenge is
destroyed rather than merely locked -- there is nothing left to brute-force.

Sends are rate-limited per destination by the shared
[core throttles](core.md#rate-limits), so a resend button cannot be turned into a
mail bomb.
