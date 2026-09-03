# The `shop` app

A self-contained storefront: a catalogue whose categories declare what their
products are, products with variants and stock, timed discount campaigns,
search and merchandising lists, reviews and likes, a basket per account, and an
order cycle that reserves stock, takes a coupon and settles a payment.

It is a **feature app**, not part of this starter's infrastructure. Copy the
directory into any Django project, or delete it from this one; nothing else
depends on it.

## The two halves, and why they are governed differently

The **catalogue** is written in the admin and nowhere else. There is no endpoint
that creates a product, on purpose: a shop's catalogue is its balance sheet, and
an API that writes to it needs an authorisation model this app does not have and
most shops do not want. So the API reads and the admin writes.

The **shopper's half** — a basket, a review, a like, an order — is the opposite:
written over the API by the account it belongs to and by nobody else. Every
queryset in that half starts from the caller, so no argument anywhere can reach
another account's basket, and an id belonging to somebody else's is a 404 rather
than a 403.

## What it needs from a project

| Requirement | Why | If it is missing |
| --- | --- | --- |
| Django 5.2+ | Model and admin APIs it uses | — |
| An account model | Baskets, reviews, likes and orders belong to one | — |
| `django-ninja` | The REST API in `rest/v1.py` | Delete `rest/`; the admin still works |
| `strawberry-graphql-django` | The GraphQL half in `graph/` | Delete `graph/`; the other doors still work |
| `django-socio-grpc` | The gRPC half in `grpc/` | Delete `grpc/`; the other doors still work |
| `django-unfold` | The admin theme | Falls back to Django's own admin — see `theme.py` |

Nothing here imports the project around it. Every setting it reads has a
default, read through a function rather than imported as a constant, so the
package works in a project that has never declared a single `SHOP_*` setting.
Refusals are raised as this app's own two exceptions and translated at each
transport's edge, so it does not need to know what a project does with them.

## Installing it elsewhere

```python
# settings.py
INSTALLED_APPS = [..., "apps.shop"]  # or wherever you put it
SHOP_CURRENCY = "GBP"  # optional; defaults to USD
SHOP_REVIEW_MODERATION = True  # optional; on by default
SHOP_MAX_ITEM_QUANTITY = 99  # optional
SHOP_PAGE_SIZE = 24  # optional
SHOP_MAX_PAGE_SIZE = 100  # optional

# urls.py / your NinjaAPI
from apps.shop.rest.v1 import router as shop_router

api.add_router("/shop", shop_router, tags=["Shop"])
```

Then `manage.py migrate`. The GraphQL and gRPC halves are picked up the same way
the rest of this project's are — `apps.shop.graph.schema` contributes a `Query`
and a `Mutation`, and `apps.shop.grpc.services.GRPC_SERVICES` a servicer.

In this project it is wired the starter's own way instead: `DJANGO_SHOP_ENABLED`
puts `apps.shop.apps.ShopConfig` into `INSTALLED_APPS` and its router into
`SHOP_ROUTERS`, which `config/api.py` attaches to every registered API version.
Naming the app is the whole installation; leaving it unset costs nothing.

## What is in here

| Module | Holds |
| --- | --- |
| `models.py` | The catalogue, the basket, reviews and likes, and the order cycle |
| `attributes.py` | The eight attribute types, and the one canonical form each value has |
| `options.py` | Every setting this app reads, each with a default |
| `pricing.py` | What a thing costs right now, and which campaign decided it |
| `payloads.py` | One shape per thing, built once and served over all three transports |
| `services.py` | Every question this app answers, decided once rather than per transport |
| `rest/v1.py` | The HTTP endpoints |
| `graph/` | The same shop as GraphQL types, queries and mutations |
| `grpc/` | The same shop as gRPC actions, and the `.proto` they generate |
| `admin.py` | Where the catalogue is actually built, and the moderation queue |
| `theme.py` | Where the admin theme comes from, and what to do without one |

## The decisions worth knowing before changing anything

**A category is the shape of a product, not a folder.** `CategoryAttribute` rows
declare what a category's products have — screen size in inches as a required
number, colour as one of a list — and attributes are inherited down the tree, so
"warranty, in months" declared on Electronics is answered by every laptop under
it. An attribute marked `is_variant` is answered per variant instead.

**A discount is a row with a window, not a column on the product.** No
`sale_price` is ever stored: the campaign is one row naming what it applies to
and when it runs, and a sale ends because a clock passed a timestamp rather than
because a script ran. Only ever one campaign applies — whichever saves the
shopper most, with `priority` breaking a tie — because stacking is how a shop
sells at a negative price the weekend two campaigns overlap.

**A basket stores quantities, not prices.** It is a list of intentions, priced
when it is read by the same code the product page used. An **order** is the
opposite: prices, tax and the delivery address are copied onto it when it is
placed, and nothing recomputes them, because it is a record of an agreement.

**Stock moves when the order is placed, not when it is paid.** Two shoppers on a
checkout page for the last one in stock must not both succeed. The decrement is
recorded as an `InventoryReservation`, and cancelling releases it exactly once.

**Ratings are cached and the reviews are the truth.** `rating_average` and
`rating_count` are columns because "four stars and up, best first" is an
ordering, and they are recomputed from the reviews in one place so the cache
cannot drift.

## Changing the gRPC surface

The `.proto` and its stubs are generated from the `@grpc_action` decorators, not
written by hand. After changing one, run `manage.py protos` and commit what it
writes; `manage.py protos --check` fails the build if they have drifted.
