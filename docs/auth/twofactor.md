# Two-factor authentication

Authenticator apps, delivered codes, and printable recovery codes. Enable it and
every login method gains a second step -- none of them had to be changed for
that to happen, because they all finish through one function that decides whether
an account still owes a factor.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/2fa/challenge` | None | Send a code for a pending sign-in |
| `POST` | `/api/v1/auth/2fa/email/confirm` | Bearer | Confirm email second-factor enrolment |
| `POST` | `/api/v1/auth/2fa/email/enroll` | Bearer | Start email second-factor enrolment |
| `GET` | `/api/v1/auth/2fa/methods` | Bearer | List the second factors on this account |
| `POST` | `/api/v1/auth/2fa/recovery/generate` | Bearer | Replace the recovery codes on this account |
| `POST` | `/api/v1/auth/2fa/sms/confirm` | Bearer | Confirm SMS second-factor enrolment |
| `POST` | `/api/v1/auth/2fa/sms/enroll` | Bearer | Start SMS second-factor enrolment |
| `POST` | `/api/v1/auth/2fa/totp/confirm` | Bearer | Confirm authenticator-app enrolment |
| `POST` | `/api/v1/auth/2fa/totp/enroll` | Bearer | Start authenticator-app enrolment |
| `POST` | `/api/v1/auth/2fa/verify` | None | Finish a sign-in with a second factor |
| `DELETE` | `/api/v1/auth/2fa/{method}` | Bearer | Turn off a second factor |
<!-- /generated:routes -->

## Models

<!-- generated:models -->
#### `RecoveryCode`

A single-use way back in when every other factor is unavailable.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | BigAuto | primary key |
| `user` | ForeignKey | → `auth.User` |
| `code_hash` | Char | not editable |
| `used_at` | DateTime | nullable |
| `created_at` | DateTime | not editable |

#### `SecondFactor`

One enrolled factor. At most one row per method per account.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `user` | ForeignKey | → `auth.User` |
| `method` | Char |  |
| `destination` | Char |  |
| `secret_encrypted` | Text | not editable |
| `last_counter` | BigInteger |  |
| `confirmed_at` | DateTime | nullable |
| `last_used_at` | DateTime | nullable |
| `created_at` | DateTime | not editable |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `RecoveryCode` | No — read-only | — | `user`, `created_at`, `used_at` |
| `SecondFactor` | Yes | — | `user`, `method`, `destination`, `is_confirmed`, `created_at`, `last_used_at` |
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_AUTH_TOTP_ISSUER` | **Yes** | the name an authenticator app shows beside the account. |
| `DJANGO_AUTH_RECOVERY_CODE_COUNT` | Optional | how many recovery codes a set contains. Range 5–30. |
<!-- /generated:settings -->

```bash
DJANGO_AUTH_METHODS=password
DJANGO_AUTH_SECOND_FACTORS=totp,sms,email,recovery
DJANGO_AUTH_TOTP_ISSUER=Your Product
```

Second factors need at least one login method; asking for factors with nothing to
put them in front of is refused at startup.

| Factor | What it is | Needs |
| --- | --- | --- |
| `totp` | An authenticator app | `pyotp` (the `totp` extra) |
| `sms` | A code texted to a verified number | A real SMS backend |
| `email` | A code mailed to the account's address | An email backend |
| `recovery` | Single-use printed codes | Nothing |

## Usage

### The login handshake

A login against an account with a confirmed factor returns no credentials:

```json
{
  "requires_second_factor": true,
  "credentials": null,
  "login_ticket": "pL9...",
  "methods": ["totp", "recovery"]
}
```

For `totp` or `recovery`, go straight to verify:

```bash
curl -X POST .../auth/2fa/verify \
  -d '{"login_ticket": "pL9...", "code": "418239", "method": "totp"}'
```

For `sms` or `email`, ask for the code first:

```bash
curl -X POST .../auth/2fa/challenge -d '{"login_ticket": "pL9...", "method": "sms"}'
curl -X POST .../auth/2fa/verify   -d '{"login_ticket": "pL9...", "code": "418239"}'
```

`method` is optional on verify and inferred where it is unambiguous -- but
`recovery` is *never* inferred. Spending a printed code on what was meant to be a
mistyped authenticator digit would burn a credential somebody may have filed away
months ago, so it is only used when asked for by name.

A wrong code does not consume the login ticket. Mistyping an authenticator code
should not throw you back to the password prompt; the ticket has its own attempt
budget and dies when that runs out.

### Enrolling an authenticator app

```bash
curl -X POST .../auth/2fa/totp/enroll -H 'Authorization: Bearer ...'
```

```json
{"secret": "JBSWY3DPEHPK3PXP", "otpauth_uri": "otpauth://totp/zoe?issuer=Your%20Product&..."}
```

Render the URI as a QR code, then confirm with a code from the app:

```bash
curl -X POST .../auth/2fa/totp/confirm -H 'Authorization: Bearer ...' -d '{"code": "418239"}'
```

Enrolment counts for nothing until confirmed. A user who scans a QR code and
never proves they can read it must not be locked out of their own account on the
next sign-in, so an unconfirmed factor is invisible to the login flow.

### Enrolling SMS or email

`enroll` returns a challenge, `confirm` settles it:

```bash
curl -X POST .../auth/2fa/sms/enroll  -d '{"phone": "+14155550101"}'
curl -X POST .../auth/2fa/sms/confirm -d '{"ticket": "hZ3...", "code": "418239"}'
```

Confirming SMS also marks the number verified in
[`auth_core.PhoneNumber`](core.md). A number already belonging to another account
is refused.

### Recovery codes

```bash
curl -X POST .../auth/2fa/recovery/generate -H 'Authorization: Bearer ...'
```

```json
{"codes": ["7K2MP-QR4WX", "..."]}
```

Shown once. The server keeps only digests from here on, and generating a new set
retires the old one immediately -- so this endpoint is also how a user invalidates
a list they think has been seen.

### Listing and removing

```bash
curl .../auth/2fa/methods -H 'Authorization: Bearer ...'
curl -X DELETE .../auth/2fa/totp -H 'Authorization: Bearer ...'
```

`methods` reports what is enrolled, what the deployment offers, and how many
recovery codes remain unused.

## Notes

An authenticator code is accepted once. `pyotp`'s own verify would let the same
code through for its whole window, so the accepted time step is recorded and
anything at or before it is refused.

TOTP secrets are stored encrypted, never returned after enrolment, and excluded
from the admin. Recovery codes are stored as salted digests.

Every attempt, enrolment and removal lands in [`AuthEvent`](core.md).
