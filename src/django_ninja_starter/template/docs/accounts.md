# Accounts

The account every login resolves to, and the profile attached to it. Always
installed: every other table in this project points at `AUTH_USER_MODEL`, and
Django's own advice is to own that model from the first migration rather than try
to swap one in later.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/users/me` | Bearer | The signed-in account and its profile |
| `PATCH` | `/api/v1/users/me/profile` | Bearer | Update the signed-in account's profile |
<!-- /generated:routes -->

## Models

<!-- generated:models -->
#### `Profile`

Everything about an account that authentication has no opinion about.

| Field | Type | Notes |
| --- | --- | --- |
| `user` | OneToOne | primary key, → `accounts.User` |
| `display_name` | Char |  |
| `avatar_url` | Char |  |
| `bio` | Text |  |
| `locale` | Char |  |
| `timezone` | Char |  |
| `date_of_birth` | Date | nullable |
| `marketing_opt_in` | Boolean |  |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |

#### `User`

A person, or a machine, that this API can recognise.

| Field | Type | Notes |
| --- | --- | --- |
| `password` | Char |  |
| `last_login` | DateTime | nullable |
| `is_superuser` | Boolean |  |
| `id` | UUID | primary key, not editable |
| `username` | Char | unique |
| `email` | Char | unique, nullable |
| `email_verified_at` | DateTime | nullable |
| `is_active` | Boolean |  |
| `is_staff` | Boolean |  |
| `date_joined` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
<!-- /generated:models -->

### Why `username` and not `email`

The obvious choice for an API is email-as-login, and this project does not make
it. It can create an account from a phone number alone — that is what the
[SMS-code method](auth/sms-code.md) is for — and an email-only model would have
to invent a fake address to do it.

So the stable identifier is a `username`: one the user picks at password signup,
or one derived for them when they arrive by code, link or provider. The address
is a separate field, optional and **unique**.

Email login still works everywhere you would expect it to:

- The [password method](auth/password.md) accepts an address in its identifier
  box and resolves it.
- The [email-code](auth/email-code.md) and [magic-link](auth/magic-link.md)
  methods resolve an address directly and never ask for a username.
- Every [social provider](oauth/core.md) matches on its own subject identifier.

`email` is nullable rather than blank for a reason worth knowing: a unique column
cannot hold two empty strings, so a second phone-only account would collide on
it. `NULL` is the only honest shape for a unique optional column. Code reading it
should expect `None`.

### The profile

Split from `User` rather than piled onto it, so the table every request reads
stays narrow, and so a project can add its own columns without touching the model
Django's auth machinery is wired to.

It is created automatically for every account, from whichever direction the
account arrived — a login method, a social callback, the admin,
`createsuperuser`, or a project's own code. That happens on `post_save` rather
than in the manager, because the manager only sees some of those. The result is a
promise the rest of the code leans on: **`user.profile` always exists.**

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `Profile` | Yes | — | `user`, `display_name`, `locale`, `timezone`, `marketing_opt_in`, `updated_at` |
| `User` | Yes | — | `username`, `email`, `email_verified`, `is_active`, `is_staff`, `date_joined` |
<!-- /generated:admin -->

`UserAdmin` subclasses Django's own rather than replacing it, so the parts that
are genuinely easy to get wrong stay right: the password renders as a hashed
value with a change link instead of a text input, and setting one goes through
the dedicated form.

Its `search_fields` is load-bearing beyond this page. Every other admin in the
project reaches an account through `autocomplete_fields = ("user",)`, and Django
refuses that unless the target admin declares what it can be searched by.

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_ACCOUNTS_DEFAULT_LOCALE` | **Yes** | the locale a new profile starts with. |
| `DJANGO_ACCOUNTS_DEFAULT_TIMEZONE` | **Yes** | the time zone a new profile starts with. |
| `DJANGO_AUTH_USER_MODEL` | **Yes** | the model every login resolves to and every table points at. |
<!-- /generated:settings -->

The defaults work with no configuration:

```bash
DJANGO_AUTH_USER_MODEL=accounts.User
DJANGO_ACCOUNTS_DEFAULT_LOCALE=en-us
DJANGO_ACCOUNTS_DEFAULT_TIMEZONE=UTC
```

### Swapping the model

`DJANGO_AUTH_USER_MODEL` is honoured, so a project can point at its own model.
Two things move with it:

- **The profile relation.** `user.profile` is what the login flows enrich and
  what `/users/me` returns. A replacement model without one keeps working —
  the enrichment helpers check before they touch anything — but loses that
  enrichment silently.
- **Email uniqueness.** The shipped model makes `email` unique, which is what
  lets an address resolve to exactly one account. A model without that constraint
  falls back to refusing an ambiguous address rather than guessing, which is
  correct but a worse experience.

If you are going to swap it, do it before the first migration.

## Usage

### Reading the signed-in account

```bash
curl https://api.example/api/v1/users/me -H 'Authorization: Bearer eyJhbGciOi...'
```

```json
{
  "id": "3f2b1c8e-...",
  "username": "zoe",
  "email": "zoe@example.com",
  "email_verified": true,
  "is_active": true,
  "is_staff": false,
  "date_joined": "2026-08-20T09:14:02Z",
  "last_login": "2026-08-25T11:02:44Z",
  "profile": {
    "display_name": "Zoe Adeyemi",
    "avatar_url": "https://lh3.example/a/...",
    "bio": "",
    "locale": "en-us",
    "timezone": "UTC",
    "date_of_birth": "",
    "marketing_opt_in": false
  }
}
```

Any method's credential works here. That is the point of them all funnelling
through one issuer — see [credentials and token modes](credentials.md).

### Updating the profile

```bash
curl -X PATCH .../users/me/profile \
  -H 'Authorization: Bearer eyJhbGciOi...' \
  -H 'Content-Type: application/json' \
  -d '{"display_name": "Zoe A.", "timezone": "Europe/Lisbon"}'
```

A partial update: an omitted field is left alone, and an empty string clears one.
That distinction is why the schema defaults to `null` rather than `""` — a PATCH
has to be able to tell "not supplied" from "set to empty" somehow.

Returns the whole account, so a client does not need a second request to see the
result.

### What a login fills in for you

Signing in with a provider fills any profile field that is still empty:

```python
from infrastructure.accounts.profiles import enrich_profile

enrich_profile(user, display_name="Zoe Adeyemi", avatar_url="https://...")
```

**Blanks only.** A provider knowing a name is not a reason to overwrite the one
somebody typed here, and signing in with Google a second time must not quietly
undo an edit made in between.

Redeeming an email code or a magic link marks the address verified, but only when
it is the address on the account:

```python
from infrastructure.accounts.profiles import confirm_email

confirm_email(user, "zoe@example.com")  # -> True the first time, False after
```

Reaching a *different* address is a fact about that address, not this one, which
is why the check is there rather than trusting whatever came back.

### Creating accounts in your own code

```python
from django.contrib.auth import get_user_model

User = get_user_model()
User.objects.create_user("zoe", email="zoe@example.com", password="…")
User.objects.create_user("zoe")  # passwordless: password set unusable
User.objects.create_superuser("root", email="root@example.com", password="…")
```

An account created without a password gets an unusable one rather than an empty
field, so it cannot be mistaken for a password that was never set. That is what
every passwordless method relies on.

## See also

- [Credentials and token modes](credentials.md) — what a login hands back.
- [Authentication core](auth/core.md) — phone numbers, the audit trail, and the
  challenge store.
