# Webhooks

`DJANGO_WEBHOOKS_ENABLED=true` installs outgoing webhooks: an account registers
https endpoints, each with the event types it wants and a secret of its own, and
the project POSTs each matching event to it -- signed, retried and recorded.

## Sending an event

Code with something to say queues it; nothing is sent inside the request:

```python
from apps.webhooks.services import emit

emit(order.customer, "shop.order.paid", {"order": str(order.id)})
```

`manage.py webhooks_deliver` sends whatever is due. Schedule it (every minute
from cron or a systemd timer is plenty). A delivery that fails is retried after
`DJANGO_WEBHOOKS_BACKOFF_SECONDS`, then twice that, four times that, and so on,
until `DJANGO_WEBHOOKS_MAX_ATTEMPTS` is reached and it is marked `failed`. Every
try is kept as a `DeliveryAttempt`, readable at
`GET /api/v1/webhooks/{id}/deliveries`.

## Verifying a delivery

Each request carries `X-Webhook-Event` and
`X-Webhook-Signature: t=<unix seconds>,v1=<hex>`, where `v1` is
HMAC-SHA256 with the endpoint's secret over `"<t>." + raw body`. Recompute it,
compare in constant time, and reject a `t` that is too old.

## SSRF

Only `https` URLs are accepted, and the host's every resolved address must be
public: private, loopback, link-local and reserved ranges are refused when the
endpoint is registered **and again before each send**. Redirects are not followed.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/webhooks` | Bearer | List this account's webhook endpoints |
| `POST` | `/api/v1/webhooks` | Bearer | Register a webhook endpoint |
| `DELETE` | `/api/v1/webhooks/{endpoint_id}` | Bearer | Delete a webhook endpoint |
| `GET` | `/api/v1/webhooks/{endpoint_id}/deliveries` | Bearer | Recent deliveries to an endpoint, with every attempt |
| `POST` | `/api/v1/webhooks/{endpoint_id}/rotate-secret` | Bearer | Replace the signing secret |
<!-- /generated:routes -->

## Settings

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_WEBHOOKS_ENABLED` | **Yes** | whether this deployment carries outgoing webhooks at all. |
| `DJANGO_WEBHOOKS_MAX_ATTEMPTS` | Optional | how many times a delivery is tried before it is marked failed. 1 or more. |
| `DJANGO_WEBHOOKS_BACKOFF_SECONDS` | Optional | the wait after the first failure; it doubles after each one. 1 or more. |
| `DJANGO_WEBHOOKS_TIMEOUT_SECONDS` | Optional | how long one delivery waits for the receiver to answer. 1 or more. |
<!-- /generated:settings -->

## Models

<!-- generated:models -->
#### `Delivery`

One event bound for one endpoint, retried until it lands or gives up.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `endpoint` | ForeignKey | → `webhooks.Endpoint` |
| `event` | Char |  |
| `payload` | JSON |  |
| `status` | Char |  |
| `attempts` | PositiveInteger |  |
| `next_attempt_at` | DateTime | nullable |
| `created_at` | DateTime | not editable |

#### `DeliveryAttempt`

What happened the one time a delivery was tried. A record, never edited.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | BigAuto | primary key |
| `delivery` | ForeignKey | → `webhooks.Delivery` |
| `number` | PositiveInteger |  |
| `status_code` | PositiveInteger | nullable |
| `error` | Text |  |
| `duration_ms` | PositiveInteger |  |
| `created_at` | DateTime | not editable |

#### `Endpoint`

An https URL an account wants told about some of its events.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `owner` | ForeignKey | → `accounts.User` |
| `url` | Char |  |
| `events` | JSON |  |
| `secret` | Char |  |
| `is_active` | Boolean |  |
| `created_at` | DateTime | not editable |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `Delivery` | Yes | — | `event`, `endpoint`, `status`, `attempts`, `next_attempt_at`, `created_at` |
| `Endpoint` | Yes | — | `url`, `owner`, `is_active`, `created_at` |
<!-- /generated:admin -->
