# The admin

Django's admin, themed with [Unfold](https://unfoldadmin.com) and arranged around
the two jobs people actually open it for: writing content, and answering a
question about an account.

Nothing here is optional or opt-in — every model this project registers is
themed, including the ones Django registers for you.

## What is different

**A front page with numbers on it.** Django's index is a list of models, which
answers "what exists" and never "what needs doing". The dashboard leads with the
figures that change what somebody does next: how many pages and fields there
are, how much of each language is written, how many *required* fields are still
empty, and how sign-ins have gone this week. Each block is permission-checked,
and each is absent rather than empty when its app is not installed.

**A sidebar that matches the deployment.** Navigation is grouped — Overview,
Content, People, Credentials, Audit — and built per request from the apps that
actually registered. A project running only password login has no Credentials
group; one running none of the authentication apps has neither Credentials nor
Audit. Every item carries a permission check, so an editor with
`cms.change_field` sees Content and nothing else rather than a menu of pages that
answer 403.

**An environment badge.** `DEBUG` is the signal, because it is already the line
between a machine somebody is developing on and one real users can reach. In
development the header wears an amber **Development** label; in production it
wears nothing.

**Lists that say more per row.** Two-line cells (name over slug, username over
email), coloured labels for status, role and field type, dropdown and radio
filters instead of a wall of links, and an unsaved-changes warning on the pages
where losing an edit costs an afternoon.

## The CMS content screen

The one page that is not a Django change form: `Pages → Edit content`. It is
built from Unfold's own layout, card component and widgets, so the boxes an
editor types into here are the boxes they type into everywhere else — a date
opens the admin's date picker, a boolean is a switch, an image is a URL field
with alt text beside it.

See [the CMS page](cms.md#admin) for what it does and who may open it.

## Configuring it

Everything visual lives in one `UNFOLD` dictionary in `src/config/settings/base.py`:
the site title, the accent colour, the border radius, whether the sidebar offers
search. Three of its values are dotted paths rather than literals, because they
are questions only a running project can answer:

| Setting | Callback | Answers |
| --- | --- | --- |
| `ENVIRONMENT` | `adminui.environment_badge` | Is this production? |
| `SIDEBAR.navigation` | `adminui.sidebar_navigation` | Which apps are installed, and what may this user open? |
| `DASHBOARD_CALLBACK` | `adminui.dashboard` | What are the numbers today? |

The first three live in `src/infrastructure/common/adminui.py`, and none of them
knows any app. They walk the installed apps looking for a contribution, which is
what makes the sidebar and the dashboard follow the enabled flags instead of a
list somebody has to remember to edit.

## An app contributes its own admin

Each app carries its navigation and its dashboard numbers in an `adminui.py`
beside its `admin.py` — the same convention `config/graph.py` uses for the
schema and `config/grpc.py` for the gRPC servicers:

```python
# apps/shop/adminui.py
from infrastructure.common.adminui import Section, card, changelist, item, may

NAVIGATION_ORDER = 40
DASHBOARD_ORDER = 40


def navigation(request):
    return {
        "title": "Shop",
        "items": [item("Orders", "receipt_long", changelist("shop", "order"), "shop.view_order")],
    }


def dashboard(request):
    if not may(request, "shop.view_order"):
        return None
    return Section(title="Shop", cards=[card("Orders this week", 12, icon="receipt_long")])
```

Both functions are optional, both are asked **per request**, and both may return
`None` for "nothing to show this person" — which is how permissions are applied.
A contributor decides what its reader may see and offers less; nothing filters
afterwards, because a filter in the project would have to know what each card
means. `may()`, `item()`, `card()` and `changelist()` are the helpers for that,
and `changelist()` reverses lazily so an app can name a model page without the
project having imported it yet.

The `*_ORDER` values place the contribution; a group may also carry its own
`order`, because *Credentials above Audit* is true however many apps fill
either. **Groups merge by title**, so several apps can put items under one
heading — every token mode contributes to Credentials — and a heading with no
items for this reader is not drawn at all.

So installing an app is the whole of installing its admin. No project file is
edited, no template block is added, and `src/templates/admin/index.html` draws
whatever it is handed without knowing an app either. `tests/test_app_isolation.py`
renders the front page with nothing enabled and with each feature app alone, and
asserts that an app contributes its section exactly when it is installed.

Unfold must stay ahead of `django.contrib.admin` in `INSTALLED_APPS` — it themes
the admin by overriding its templates, and a template is found in app order. A
test asserts that ordering, because behind the admin it silently themes nothing.

## Extending it

New model admins should extend Unfold's bases rather than Django's:

```python
from unfold.admin import ModelAdmin, TabularInline


@admin.register(Invoice)
class InvoiceAdmin(ModelAdmin): ...
```

A plain `admin.ModelAdmin` still works and still registers; it renders as an
unstyled page in the middle of a themed one, which reads as a bug in the theme.
The same applies to anything a third-party app registers — Django's own `Group`
admin is re-registered in `infrastructure/accounts/admin.py` for exactly this
reason, and it is the pattern to copy.
