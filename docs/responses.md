# The response envelope

Every JSON body this API sends, in one shape, so a client parses it once.

```json
{
  "errors": null,
  "data": { "token_type": "bearer", "access_token": "eyJhbGciOi..." },
  "isSuccess": true,
  "statusCode": 200,
  "title": "SUCCESS",
  "description": "The request succeeded."
}
```

The same six keys come back when it goes wrong. Nothing moves; `data` is `null`
and `errors` is filled in:

```json
{
  "errors": ["Those credentials are not valid."],
  "data": null,
  "isSuccess": false,
  "statusCode": 401,
  "title": "INVALID_CREDENTIALS",
  "description": "Those credentials are not valid."
}
```

> Every other page in these docs shows the **payload** — what lands in `data`.
> They would be unreadable if each example repeated the wrapper.

## The six keys

| Key | Type | Meaning |
| --- | --- | --- |
| `errors` | `string[]` or `null` | Every reason the request failed. `null` — never `[]` — when it did not. |
| `data` | anything, or `null` | What the endpoint returns. Its schema is the one on the endpoint's own page. |
| `isSuccess` | boolean | `statusCode < 400`. The one field worth branching on. |
| `statusCode` | integer | The HTTP status, repeated in the body for clients that cannot see it. |
| `title` | enum | A stable key naming *what happened*. See below. |
| `description` | string | One English sentence, for a developer reading a log. Not translated, not for end users. |

## `title` is the part to build on

A status code says a request failed; it does not say a code expired rather than
being mistyped, and neither does an English sentence a client cannot translate.
`title` is a member of a fixed enum, so a client can key its own translations off
it and show a message in the user's language:

```ts
const message = messages[body.title] ?? messages.BAD_REQUEST;
```

Members are added, never reworded: renaming one silently breaks every client that
translated it. `description` may be reworded freely — nothing should depend on it.

### Successes

| Title | Means |
| --- | --- |
| `SUCCESS` | The request succeeded. |
| `CREATED` | The resource was created. |

### Refusals the status already describes

| Title | Means |
| --- | --- |
| `BAD_REQUEST` | The request was refused. |
| `VALIDATION_ERROR` | The request body did not validate. |
| `AUTHENTICATION_REQUIRED` | This endpoint needs a credential. |
| `FORBIDDEN` | This account may not do that. |
| `NOT_FOUND` | There is no such thing to act on. |
| `CONFLICT` | That would conflict with something that already exists. |
| `GONE` | What the request refers to has expired. |
| `RATE_LIMITED` | Too many attempts. Wait and try again. |
| `SERVICE_UNAVAILABLE` | A dependency this endpoint needs is unavailable. |
| `INTERNAL_ERROR` | The request failed for a reason the client cannot fix. |

### Accounts and passwords

| Title | Means |
| --- | --- |
| `INVALID_CREDENTIALS` | The identifier or the password is wrong. |
| `INVALID_IDENTIFIER` | That is not a usable email address or phone number. |
| `USERNAME_REQUIRED` | A username is required. |
| `ACCOUNT_EXISTS` | An account already uses that identifier. |
| `ACCOUNT_NOT_FOUND` | No account uses that identifier. |
| `ACCOUNT_DISABLED` | The account exists but may not sign in. |
| `PHONE_IN_USE` | Another account already uses that phone number. |
| `ACCOUNT_HAS_NO_EMAIL` | The account has no email address to send to. |
| `WEAK_PASSWORD` | The password does not meet the policy. |
| `INCORRECT_PASSWORD` | The current password given is not this account's. |

### Codes, and the sign-ins they belong to

| Title | Means |
| --- | --- |
| `INVALID_CODE` | The code is wrong. |
| `CODE_NOT_REQUESTED` | Ask for a code before trying to verify one. |
| `CODE_EXPIRED` | The code has expired. Ask for another. |
| `TOO_MANY_ATTEMPTS` | Too many wrong attempts. Start again. |
| `CHALLENGE_INVALID` | The ticket is unknown or no longer usable. |
| `SIGN_IN_EXPIRED` | This sign-in is no longer valid. Start again. |

### Second factors

| Title | Means |
| --- | --- |
| `SECOND_FACTOR_UNSPECIFIED` | The request must say which second factor it means. |
| `SECOND_FACTOR_NOT_ENABLED` | This deployment does not offer that second factor. |
| `SECOND_FACTOR_NOT_SET_UP` | This account has not set that second factor up. |
| `SECOND_FACTOR_EXISTS` | That second factor is already set up. |
| `SECOND_FACTOR_NOT_CODE_BASED` | That second factor does not use a sent code. |
| `SECOND_FACTOR_NO_DESTINATION` | That second factor has no destination on file. |
| `ENROLMENT_NOT_STARTED` | Begin enrolment before confirming it. |

### Credentials and sessions

| Title | Means |
| --- | --- |
| `TOKEN_REQUIRED` | A token is required and none was given. |
| `TOKEN_INVALID` | The token is not one this deployment issued. |
| `TOKEN_EXPIRED` | The token has expired. |
| `TOKEN_REUSED` | A spent token was presented again; the session was ended. |
| `TOKENS_UNSUPPORTED` | This deployment does not issue that kind of credential. |
| `SESSION_ENDED` | The session has ended. Sign in again. |
| `SESSION_NOT_FOUND` | No such session. |
| `NOT_SIGNED_IN` | There is no signed-in session to act on. |

### Social sign-in

| Title | Means |
| --- | --- |
| `OAUTH_NOT_CONFIGURED` | This provider has no credentials configured. |
| `OAUTH_STATE_MISSING` | The callback arrived without its state. |
| `OAUTH_FAILED` | The provider did not complete the sign-in. |

## Writing an endpoint

Nothing. Return the schema you would have returned anyway:

```python
@router.get("/me", response=AccountOut, auth=api_auth)
def me(request: HttpRequest) -> AccountOut:
    return _account_out(request.user)
```

The wrapping happens in `EnvelopeRenderer` — `src/infrastructure/common/responses.py` —
at the moment the body is serialised, which is why it cannot be forgotten on the
one endpoint nobody tested, and why a feature app added with `startapi` is
enveloped from its first request.

### Failing

Raise `ApiError` (or `AuthError`, which is one) with the title a client should
translate. Leave the title off where the status says everything:

```python
from infrastructure.common.errors import ApiError
from infrastructure.common.responses import ResponseTitle

raise ApiError("That refresh token has expired.", status=401, title=ResponseTitle.TOKEN_EXPIRED)
raise ApiError("Supply at least one field to update.", status=400)
```

The message becomes both the single entry in `errors` and the `description`.
Pass `errors=[...]` to report several at once.

### Adding a title

Add the member to `ResponseTitle` and its English gloss to `DESCRIPTIONS`, both in
`src/infrastructure/common/responses.py`. A test fails if a member has no gloss.

## What is *not* enveloped

- **`/api/{version}/openapi.json` and `/api/docs`.** The schema document is
  itself the contract; wrapping it would break every tool that reads it.
- **Redirects.** The OAuth `/start` and `/callback` routes end in a `302` with no
  body. When one of them fails it answers JSON, and that JSON *is* enveloped.
- **Unhandled exceptions.** Django Ninja hands a bug back to Django rather than
  answering with it, so a genuine `500` is Django's response, not this one. Every
  refusal the API decides on — including `401` from a missing credential and
  `422` from a body that did not validate — comes back in the envelope.

## In Swagger

`/api/docs` shows the envelope, not the payload, because the published OpenAPI
document is rewritten on its way out: each documented response becomes the
wrapper with the endpoint's own schema inlined at `data`, and every operation
gains a `default` response for the errors any endpoint can raise. `title` points
at one shared `ResponseTitle` schema, so a generated client gets the enum as an
enum.
