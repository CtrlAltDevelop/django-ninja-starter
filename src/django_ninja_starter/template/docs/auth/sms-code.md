# SMS-code login

Sign-up and sign-in with a one-time code sent to a phone number.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `POST` | `/api/v1/auth/sms-code/login/start` | None | Send a sign-in code by SMS |
| `POST` | `/api/v1/auth/sms-code/login/verify` | None | Sign in with an SMS code |
| `POST` | `/api/v1/auth/sms-code/logout` | None | Sign out |
| `POST` | `/api/v1/auth/sms-code/signup/start` | None | Send a sign-up code by SMS |
| `POST` | `/api/v1/auth/sms-code/signup/verify` | None | Create an account with an SMS code |
<!-- /generated:routes -->

## Models

<!-- generated:models -->
_This app defines no models of its own._
<!-- /generated:models -->

Numbers live in [`auth_core.PhoneNumber`](core.md), not here: the SMS second
factor needs the same verified number, and a project may enable either one
without the other.

## Admin

<!-- generated:admin -->
_This app registers nothing in the admin._
<!-- /generated:admin -->

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_AUTH_SMS_BACKEND` | **Yes** | the carrier that delivers the text. |
| `DJANGO_AUTH_SMS_FROM` | Recommended | the sender ID or number the message comes from. |
<!-- /generated:settings -->

```bash
DJANGO_AUTH_METHODS=sms_code
DJANGO_AUTH_SMS_BACKEND=yourproject.sms.TwilioBackend
DJANGO_AUTH_SMS_FROM=+15555550100
```

The default backend writes codes to the log instead of sending them. That is fine
locally and a full account takeover in production -- anyone who can read logs can
sign in as anyone -- so the system checks warn about it whenever SMS is in use.

A backend is any object with `send(destination, body)`:

```python
class TwilioBackend:
    def send(self, destination: str, body: str) -> None:
        client.messages.create(to=destination, from_=settings.AUTH_SMS_FROM, body=body)
```

## Usage

```bash
curl -X POST .../auth/sms-code/login/start -d '{"phone": "+1 (415) 555-0101"}'
```

```json
{"ticket": "hZ3...", "channel": "sms", "destination": "***0101", "expires_in": 300}
```

Numbers are normalised to E.164 before anything is compared or stored, tolerating
the spacing people actually type. A number that cannot be read as international
format is refused with an explanation rather than silently mangled.

```bash
curl -X POST .../auth/sms-code/login/verify -d '{"ticket": "hZ3...", "code": "418239"}'
```

## Notes

Reaching a number proves control of it, so a successful verification is also what
marks the number verified -- including one that was attached to the account
earlier but never confirmed. No row is created at that point: the account was
resolved *through* that row, so reaching the number only ever promotes a pending
one.

Sign-up refuses a number that already has an account, verified or not.

SMS is the most expensive channel here and the easiest to abuse. The per-
destination cooldown and hourly ceiling from
[core throttles](core.md#rate-limits) apply, and the cooldown is charged before
the carrier is called so an outage cannot become an unmetered send loop.
