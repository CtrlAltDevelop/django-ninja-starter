# Common

The foundation every project built from this starter carries, and the only app
besides [accounts](accounts.md) that is always installed. Nothing here is a
feature; everything here is what the features stand on.

| Piece | What it is |
| --- | --- |
| Health endpoints | The two questions a load balancer and an orchestrator ask |
| [The response envelope](responses.md) | One shape for every JSON body, and the `ResponseTitle` enum a client translates |
| `ApiError` | One exception for "the client did something the API must refuse" |
| The API registry | The declarative list of versions and routers, and `startapi` |
| Settings contracts | How an app states what it cannot work without, checked by `manage.py check` |
| [The admin's chrome](admin.md) | Its navigation, its environment badge and its dashboard |
| `authdocs` | The command that writes the reference half of these pages |

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health/live` | None | Liveness check |
| `GET` | `/api/v1/health/ready` | None | Readiness check |
<!-- /generated:routes -->

`live` answers "is this process running?" and never touches the database — a
liveness probe that fails when the database is slow gets the process killed for
somebody else's outage. `ready` answers "should traffic come here?" and does
check, returning `503` when it cannot.

## Models

<!-- generated:models -->
_This app defines no models of its own._
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
_This app registers nothing in the admin._
<!-- /generated:admin -->

The app registers nothing itself, but two of its modules decide how every other
app's admin looks and behaves:

- `infrastructure/common/admin.py` — `ReadOnlyAdmin` for audit tables, which are
  worthless as evidence if anybody can edit them, and `RevocableAdmin` for
  credential tables, where there is nothing meaningful to change and one very
  meaningful thing to *do*.
- `infrastructure/common/adminui.py` — the sidebar, the environment badge and the
  dashboard, all built per request from the apps that are actually installed.
  [The admin page](admin.md) covers it.

## Setup

<!-- generated:settings -->
_This app requires no settings of its own._
<!-- /generated:settings -->

## Using it

### Adding a versioned API

Feature APIs are registered declaratively, in `src/config/api_registry.json`, and
scaffolded by a command that writes the entry for you:

```bash
python manage.py startapi orders --api-version v1
```

That creates `src/apps/orders/`, an endpoint with a test, and the registry entry
that installs the app and mounts its router. `config/api.py` builds one
`NinjaAPI` per registered version and attaches every registry router plus the
routers each optional app publishes, so a new version is a key in one JSON file.

### Naming a group in Swagger

Every router is attached under a tag, and the tag is declared beside the router
that owns it -- in `api_registry.json` for a feature app, in `settings/base.py`
for the apps the starter ships:

```json
{
  "prefix": "/orders",
  "router": "apps.orders.rest.v1.router",
  "tag": "Orders",
  "description": "Place an order, read one back, and cancel one."
}
```

`config/api.py` collects those into the document's own `tags` list, which is
what makes Swagger's groups more than an alphabetical pile of accordions: the
`description` is printed under the group heading, and the order the routers are
attached in is the order the groups appear. A tag belonging to an app this
deployment did not enable is not in the list at all, for the same reason its
routes are not.

### Refusing a request

```python
from infrastructure.common.errors import ApiError
from infrastructure.common.responses import ResponseTitle

raise ApiError("That code has expired.", status=410, title=ResponseTitle.CODE_EXPIRED)
```

Raised anywhere, rendered in one place, and always in the envelope. `title` is
the part a client keys its own translations off; the English sentence beside it
is for whoever is reading the log. [The response contract](responses.md) lists
every title.

### Declaring what an app needs

```python
class AuthMagicLinkConfig(AppConfig):
    settings_spec = AppSettings(
        title="Magic-link login",
        requirements=(
            Requirement(
                "AUTH_MAGIC_LINK_BASE_URL",
                env="DJANGO_AUTH_MAGIC_LINK_BASE_URL",
                purpose="the page that reads the token out of the URL",
                required=True,
            ),
        ),
    )
```

One system check reads every installed app's declaration, so a deployment is
told what the apps *it turned on* still need and nagged about nothing else.
Message ids are the app label plus the setting, so `SILENCED_SYSTEM_CHECKS` can
turn off exactly one.

An app meant to be copied into other projects — the [CMS](cms.md) is the one
here — cannot import `AppSettings` without dragging this project along, so it may
declare `settings_docs` on its `AppConfig` instead: plain rows that document its
settings without validating them.

### Keeping these pages honest

```bash
DJANGO_SETTINGS_MODULE=config.settings.test python manage.py authdocs
```

Every **Routes**, **Models**, **Admin** and **Setup** section in this
documentation is generated from the code between markers, and the test suite
runs the same command with `--check`. Prose outside the markers is never
touched. [The index](README.md#keeping-these-pages-honest) explains the split.
