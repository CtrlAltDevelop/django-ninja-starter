# Password login

Sign-up, sign-in, reset and change with a password. The most conventional method
here, and the only one whose secret the user chooses.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/password/change` | Bearer | Change the password on this account |
| `POST` | `/api/v1/auth/password/forgot` | None | Request a password reset code |
| `POST` | `/api/v1/auth/password/login` | None | Sign in with a password |
| `POST` | `/api/v1/auth/password/logout` | None | Sign out |
| `POST` | `/api/v1/auth/password/reset` | None | Set a new password with a reset code |
| `POST` | `/api/v1/auth/password/signup` | None | Create an account with a password |
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
| `DJANGO_AUTH_PASSWORD_RESET_BASE_URL` | Recommended | the page a reset email points at. |
| `DJANGO_AUTH_EMAIL_BACKEND` | **Yes** | how a reset code is delivered. |
| `DJANGO_AUTH_EMAIL_FROM` | **Yes** | the sender address on a reset email. |
<!-- /generated:settings -->

```bash
DJANGO_AUTH_METHODS=password
DJANGO_AUTH_EMAIL_FROM=sign-in@yourdomain.example
DJANGO_AUTH_PASSWORD_RESET_BASE_URL=https://yourdomain.example/reset
```

Passwords are validated against the project's own `AUTH_PASSWORD_VALIDATORS`, so
policy is configured where Django already configures it and not twice.

## Usage

### Sign up

```bash
curl -X POST https://api.example/api/v1/auth/password/signup \
  -H 'Content-Type: application/json' \
  -d '{"identifier": "zoe", "password": "corr3ct-horse-battery", "email": "zoe@example.com"}'
```

```json
{
  "requires_second_factor": false,
  "credentials": {
    "token_type": "bearer",
    "access_token": "eyJhbGciOi...",
    "refresh_token": "eyJhbGciOi...",
    "expires_in": 3600,
    "session_id": "3f2b..."
  },
  "login_ticket": "",
  "methods": []
}
```

`identifier` is whatever your user model's `USERNAME_FIELD` is. Where that field
is not `email`, pass `email` as well and it is stored alongside.

### Sign in

```bash
curl -X POST https://api.example/api/v1/auth/password/login \
  -H 'Content-Type: application/json' \
  -d '{"identifier": "zoe", "password": "corr3ct-horse-battery"}'
```

The response has the same shape as sign-up. If the account has a confirmed second
factor, `requires_second_factor` is `true` and you get a `login_ticket` instead of
credentials -- see [two-factor authentication](twofactor.md).

The identifier box accepts a username or, where the username field is not
`email`, an email address. An address shared by several accounts is refused as
ambiguous rather than resolved: picking one would hand an attacker whichever
account sorts first.

### Reset a forgotten password

Two steps. The first is deliberately uninformative:

```bash
curl -X POST .../auth/password/forgot -d '{"email": "zoe@example.com"}'
```

```json
{
  "detail": "If that address has an account, a reset code is on its way.",
  "ticket": "hZ3...",
  "expires_in": 300
}
```

An address with no account gets the same shape, the same status and a real
ticket -- one bound to a code that was never sent anywhere. The ticket is
worthless without the code, so handing one out costs nothing, and the response
cannot be used to enumerate which addresses are registered.

```bash
curl -X POST .../auth/password/reset \
  -d '{"ticket": "hZ3...", "code": "418239", "password": "n3w-passphrase-here"}'
```

A successful reset revokes every live credential for the account, across all
three token-mode tables. Somebody resetting a password usually believes their old
one is compromised, and leaving yesterday's sessions running would defeat the
exercise.

### Change a known password

```bash
curl -X POST .../auth/password/change \
  -H 'Authorization: Bearer eyJhbGciOi...' \
  -d '{"current_password": "corr3ct-horse-battery", "new_password": "n3w-passphrase-here"}'
```

Also revokes everything, including the token that made the call. That is
intentional: the response says "Sign in again."

### Sign out

```bash
curl -X POST .../auth/password/logout -d '{"token": "eyJhbGciOi..."}'
```

Either half of a credential pair is accepted, and an absent or already-expired
token still reads as success. A client signing out should never be told its
logout failed.

## Notes

A missing account costs the same time as a wrong password: the service hashes a
throwaway value when no account matches. Without that, response time alone tells
an attacker which identifiers are real, which is most of the work of enumerating
a user base.

Repeated attempts against one identifier are capped, and both failures and
successes land in [`AuthEvent`](core.md).
