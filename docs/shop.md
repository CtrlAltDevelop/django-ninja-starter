# Shop

A storefront: a catalogue whose categories decide what their products are,
products sold in variants and by several sellers, timed discount campaigns,
search and merchandising lists, reviews and likes, a basket per account, and an
order cycle that reserves stock, issues an invoice and settles a payment.
Optional in the same way every login method is: naming it in
`DJANGO_SHOP_ENABLED` is what installs it, and a project that does not name it
carries no shop tables, no routes and never imports the package.

Like the [CMS](cms.md) and [notifications](notifications.md), it is a **feature
app**: it lives in `src/apps/shop`, and the directory can be copied into another
Django project or deleted from this one, and neither leaves a hole. Its own
[`README`](../src/apps/shop/README.md) is the drop-it-in-elsewhere guide.

## Two halves, governed differently

The **catalogue** is written in the admin and nowhere else. There is no endpoint
that creates a product, and that is a decision rather than an omission: a shop's
catalogue is its balance sheet, and an API that writes to it needs an
authorisation model this app does not have and most shops do not want. So the
API reads and the admin writes.

The **shopper's half** — a basket, a review, a like, an order — is the opposite:
written over the API by the account it belongs to and by nobody else. Every
queryset in that half starts from the caller, so no argument anywhere can widen
it, and an id belonging to somebody else's basket is a 404 rather than a 403.
"That exists, but not for you" is a sentence that has already leaked something.

## Routes

<!-- generated:routes -->
| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/shop/addresses` | Bearer | List your saved addresses |
| `POST` | `/api/v1/shop/addresses` | Bearer | Save a delivery address |
| `DELETE` | `/api/v1/shop/addresses/{address_id}` | Bearer | Forget an address |
| `GET` | `/api/v1/shop/addresses/{address_id}` | Bearer | Read one address |
| `PATCH` | `/api/v1/shop/addresses/{address_id}` | Bearer | Edit an address |
| `PUT` | `/api/v1/shop/addresses/{address_id}/default` | Bearer | Choose the default address |
| `GET` | `/api/v1/shop/brands` | None | List every brand |
| `DELETE` | `/api/v1/shop/cart` | Bearer | Empty your basket |
| `GET` | `/api/v1/shop/cart` | Bearer | Read your basket |
| `POST` | `/api/v1/shop/cart/coupon` | Bearer | Try a coupon code |
| `POST` | `/api/v1/shop/cart/items` | Bearer | Add to your basket |
| `DELETE` | `/api/v1/shop/cart/items/{item_id}` | Bearer | Remove a line |
| `PATCH` | `/api/v1/shop/cart/items/{item_id}` | Bearer | Change a line's quantity |
| `GET` | `/api/v1/shop/categories` | None | List the category tree |
| `GET` | `/api/v1/shop/categories/{slug}` | None | Read one category |
| `POST` | `/api/v1/shop/checkout` | Bearer | Create a pending order |
| `GET` | `/api/v1/shop/collections` | None | List curated collections |
| `GET` | `/api/v1/shop/collections/{slug}` | None | Read one collection |
| `GET` | `/api/v1/shop/favourites` | Bearer | Everything you have liked |
| `GET` | `/api/v1/shop/listings` | None | List the named lists |
| `GET` | `/api/v1/shop/listings/{key}` | None | Read one named list |
| `GET` | `/api/v1/shop/orders` | Bearer | List your orders |
| `GET` | `/api/v1/shop/orders/{number}` | Bearer | Read one of your orders |
| `POST` | `/api/v1/shop/orders/{number}/cancel` | Bearer | Cancel an unpaid order |
| `GET` | `/api/v1/shop/orders/{number}/invoice` | Bearer | Read an order's invoice |
| `POST` | `/api/v1/shop/orders/{number}/payment/confirm` | Bearer | Confirm a payment |
| `GET` | `/api/v1/shop/products` | None | Search and filter the catalogue |
| `GET` | `/api/v1/shop/products/{slug}` | None | Read one product |
| `DELETE` | `/api/v1/shop/products/{slug}/like` | Bearer | Unlike a product |
| `PUT` | `/api/v1/shop/products/{slug}/like` | Bearer | Like a product |
| `GET` | `/api/v1/shop/products/{slug}/related` | None | Other products like this one |
| `DELETE` | `/api/v1/shop/products/{slug}/reviews` | Bearer | Take back your review |
| `GET` | `/api/v1/shop/products/{slug}/reviews` | None | Read a product's reviews |
| `POST` | `/api/v1/shop/products/{slug}/reviews` | Bearer | Write or replace your review |
| `POST` | `/api/v1/shop/products/{slug}/view` | None | Record that somebody looked |
| `GET` | `/api/v1/shop/reviews/mine` | Bearer | Everything you have reviewed |
| `GET` | `/api/v1/shop/sellers` | None | List every seller |
| `GET` | `/api/v1/shop/sellers/{slug}` | None | Read one seller |
| `GET` | `/api/v1/shop/shipping-methods` | None | List delivery options |
<!-- /generated:routes -->

The catalogue half needs no credential. The three routes that are public *and*
personal — a product page, a review list, a recorded view — read a bearer token
where one is offered and never demand one, so the same page serves a
signed-out shopper with the heart unfilled.

## Several sellers of one thing

A product row **is** the primary seller's offer: its `seller`, its `price`, its
`stock`. Every other seller who can fill the order is a `ProductOffer` with a
price and stock of its own. A shop with one seller therefore never touches the
offer table, and a marketplace is the same tables with rows in it.

What a shopper is quoted is the **buy box**: the cheapest live seller *after*
campaigns, with a tie going to whoever dispatches sooner. Deciding it after the
discount rather than before is what stops the cheapest listing on the page
becoming the dearest the minute a sale starts. A campaign reaches a seller's
offer exactly as it reaches the shop's own price, because "20% off everything in
Kitchen" is the shop's decision about a category, not about one price column.

Adding to a basket without naming a seller takes the buy box — so the price in
the basket is the price that was on the page — and naming one is how a shopper
buys from somebody dearer with a shorter lead time. Two sellers of one thing are
two basket lines, and each line's stock check, price and eventual order line
follow the seller it names.

## Which size, from whom

A product sold in variants asks three questions at once, and the product page
answers all three in one document.

**What can be picked** is `variant_attributes`: one entry per axis the category
declares, each carrying the values this product is actually made in — a category
that allows four colours and a product that comes in two offers two, ordered by
the declared list so every product in a category prints S, M, L in that order.

**Which of those picks are still buyable** is each value's `in_stock`, and the
`variants` it names. That is the answer a size picker needs in order to grey a
button out, and computing it here rather than leaving every storefront to derive
it by scanning variants is the difference between one rule and one per client.

**Who is holding them** is on the variant: `sellers` is that variant's slice of
the page's offers, already priced, and `seller_count` counts the shop itself
among them. `stock` is the shop's own shelf and `total_stock` is every shelf,
because a page with only the first would print "out of stock" over a size three
other shops are holding.

Availability is decided in one place — `pricing.shelf`, `can_fill`,
`total_stock` and `is_available`. Three tables carry a `stock` column and they
are not alternatives: the product row is the primary seller's shelf, a variant's
is that size in that colour, and an offer's is somebody else's warehouse. Most
specific wins, exactly as the price does, and the payloads, the basket's "can
this still be filled" and the checkout's stock check all ask through the same
functions rather than each writing the comparison out again.

## What a price is

There is no `sale_price` column anywhere, and nothing caches a computed price.
A campaign is one row saying how much off what, between which dates; the
product's own price is never overwritten; and a sale ends because a clock passed
a timestamp rather than because a script ran. The obvious alternative —
`sale_price` and `sale_ends_at` on the product — breaks the first weekend a shop
runs "20% off Kitchen": somebody writes a loop over nine hundred products, and a
second loop to put them back, and the second loop is the one that gets
interrupted.

**Only one campaign ever applies**: whichever saves the shopper most, with
`priority` breaking a tie. Stacking is the alternative, and it is how a shop
sells at a negative price the weekend two campaigns overlap.

The cost is a query for the running campaigns, which is why every pricing
function takes a pre-loaded list — a listing of forty products loads them once
and hands the same list to all forty.

## A category is a shape, not a folder

`CategoryAttribute` rows hang off a category and declare what its products have:
screen size in inches as a required number, colour as one of a list. A product
fills them in and the value is validated against the declared type; an attribute
marked as distinguishing variants is answered per variant instead, because "3
left" is meaningless for a shirt that exists in four sizes.

Only a **choice** or a **colour** can distinguish variants. An option is the key
a shopper picks by, so free text would make "Red" and "Red " two variants, two
shelves and one picker offering the same colour twice; a choice is bounded by
the category's own list and a colour by the swatches somebody entered. This is
also what keeps a shoe size and a shirt size from ever meeting: they are two
attributes on two categories, each with its own list, and a book's translation
and edition are two more on a third.

Attributes are inherited down the tree, so "warranty, in months" declared on
Electronics is answered by every laptop underneath it, and a child declaring the
same code overrides it. The alternative — a products table with forty nullable
columns, or an untyped bag of JSON — means either a migration every time the
shop starts selling a new kind of thing, or a storefront defending against every
shape at render time.

The eight attribute types and what each accepts are in
[`attributes.py`](../src/apps/shop/attributes.py); the same normaliser runs for
an admin form, a JSON import and a data migration.

## Listings

The named lists a front page puts up are filters and orderings over the live
catalogue rather than stored lists, so none of them can go stale, and a shop gets
all of them without curating anything:

| Key | What it is |
| --- | --- |
| `featured` | Chosen by the shop to be shown first |
| `bestsellers` | The most units sold |
| `popular` | The most liked |
| `top_rated` | The best reviewed, counting only products somebody has reviewed |
| `newest` | The most recently added |
| `on_sale` | Everything a campaign is running on right now |
| `in_stock` | What can be bought today, from anybody |

`GET /api/v1/shop/listings` publishes the keys, so a storefront does not
hard-code them. `on_sale` is the one that cannot be expressed as an ordering —
whether a product is discounted is decided by a clock against campaign rows — so
it is filtered in Python over the live catalogue, and that is the honest cost of
not storing a sale price.

A **collection** is the other kind of list: one somebody chose by hand, in the
order they chose. The computed listings answer "what is true of the catalogue";
a collection answers "what do we want to say this week", and no ordering derived
from the data produces it.

## Baskets, orders and stock

A basket stores **quantities, not prices**. Snapshotting the price when something
is added looks like protecting the shopper and is really a way to sell last
month's price, and to hold a basket at a discount that ended. The basket is a
list of intentions, priced when it is read, by the same code the product page
used.

An **order is the opposite**: the delivery address, each line's price, its tax
and its seller are copied onto it when it is placed, and nothing recomputes them.
A shopper editing their address book must not rewrite an order that has already
shipped, and a sale ending must not change what somebody agreed to pay.

**Stock moves when the order is placed, not when it is paid**, because two
shoppers on a checkout page for the last one must not both succeed. Each
decrement is recorded as an `InventoryReservation` naming whose shelf it came
off — the product's, a variant's or a seller's — and cancelling releases it
exactly once. `released_at` rather than deleting the row, so a cancellation can
tell "already released" from "never reserved".

## Invoicing and payment

Checkout does four things in one transaction: it writes the order and its lines,
reserves the stock, issues an **invoice**, and opens a **payment** to be settled.

The invoice is issued when the order is placed rather than when it is paid,
because it is the demand for payment — a shopper paying by transfer needs the
document before the money moves. It carries a number of its own rather than the
order's, since a shop that ever issues a credit note needs its own sequence, and
it carries no totals: everything printed on it is already on the order, and
copying the numbers would be a second place for them to disagree.

**No payment gateway is wired up.** Orders are created against the `manual`
provider and somebody with a bank statement in front of them settles them in the
admin — "Mark as paid" or "Mark as rejected" on the payment or the order screen.
Those are actions rather than an editable status field, because each has
consequences beyond a status: marking an order paid turns its reservations into
sales, and a field somebody could type over would let the two disagree.

Rejecting a payment records that **one attempt** failed and opens a fresh pending
one, leaving the order payable and its stock reserved: a shopper whose card was
declined tries another, and releasing the stock underneath them would mean the
retry oversells. Cancelling the order is the separate act that puts the stock
back.

`POST /api/v1/shop/orders/{number}/payment/confirm` is the seam a real gateway's
callback is pointed at when a project has one. It settles the order through the
same service method the admin action calls, so there is one place an order
becomes paid however that was decided — and it is idempotent, because a provider
that retries its webhook must settle the same order rather than selling the stock
twice.

## Reviews and likes

One review per account per product, replaced rather than refused when somebody
writes a second: "you have already reviewed this" is a thing they then have to go
and fix by hand. Ratings are cached on the product as `rating_average` and
`rating_count`, because "four stars and up, best first" is an ordering and an
ordering cannot be a Python property; they are recomputed from the reviews in
one function, so the cache cannot drift by being updated in five places.

Moderation is on by default (`DJANGO_SHOP_REVIEW_MODERATION`) and is a **state,
not a deletion**. A rejected review stays, because the shopper who wrote it and
the moderator who rejected it are two people having a disagreement and deleting
one side of it leaves the shop unable to answer what happened. `GET
/api/v1/shop/reviews/mine` includes what is still in the queue: you are the one
person entitled to know your own review exists.

A review never publishes an address. The author is a display name, because
reviews are the most-read and most-scraped page a shop has.

## Models

<!-- generated:models -->
#### `Address`

Somewhere to send it, kept so a shopper types it once.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `user` | ForeignKey | → `accounts.User` |
| `label` | Char |  |
| `full_name` | Char |  |
| `phone` | Char |  |
| `country` | Char |  |
| `province` | Char |  |
| `city` | Char |  |
| `postal_code` | Char |  |
| `line1` | Char |  |
| `line2` | Char |  |
| `is_default` | Boolean |  |

#### `Brand`

Who makes the thing. Its own row, because shoppers filter by it.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `is_active` | Boolean |  |
| `name` | Char | unique |
| `slug` | Slug | unique |
| `description` | Text |  |
| `logo` | Char |  |
| `website` | Char |  |
| `order` | PositiveInteger |  |

#### `Cart`

One account's basket. There is exactly one, and it is never deleted.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `user` | OneToOne | unique, → `accounts.User` |

#### `CartItem`

One line of one basket: this many of this product, in this variant, from

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `cart` | ForeignKey | → `shop.Cart` |
| `product` | ForeignKey | → `shop.Product` |
| `variant` | ForeignKey | → `shop.ProductVariant`, nullable |
| `offer` | ForeignKey | → `shop.ProductOffer`, nullable |
| `quantity` | PositiveInteger |  |

#### `Category`

A branch of the catalogue, and the shape of the products on it.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `is_active` | Boolean |  |
| `parent` | ForeignKey | → `shop.Category`, nullable |
| `name` | Char |  |
| `slug` | Slug | unique |
| `description` | Text |  |
| `image` | Char |  |
| `icon` | Char |  |
| `order` | PositiveInteger |  |
| `meta_title` | Char |  |
| `meta_description` | Char |  |
| `meta_keywords` | JSON |  |

#### `CategoryAttribute`

One thing every product in a category has an answer for.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `category` | ForeignKey | → `shop.Category` |
| `name` | Char |  |
| `code` | Slug |  |
| `attribute_type` | Char |  |
| `unit` | Char |  |
| `choices` | JSON |  |
| `required` | Boolean |  |
| `is_variant` | Boolean |  |
| `is_filterable` | Boolean |  |
| `help_text` | Char |  |
| `order` | PositiveInteger |  |

#### `Collection`

A list somebody chose by hand: "Ready for winter", "Staff picks".

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `is_active` | Boolean |  |
| `name` | Char |  |
| `slug` | Slug | unique |
| `description` | Text |  |
| `image` | Char |  |
| `order` | PositiveInteger |  |

#### `CollectionItem`

One product's place in one collection. A through model, because order matters.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `collection` | ForeignKey | → `shop.Collection` |
| `product` | ForeignKey | → `shop.Product` |
| `order` | PositiveInteger |  |

#### `Coupon`

A code a shopper types, worth a percentage or an amount off.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `code` | Char | unique |
| `percent` | Decimal | nullable |
| `amount` | Decimal | nullable |
| `minimum_subtotal` | Decimal |  |
| `starts_at` | DateTime | nullable |
| `ends_at` | DateTime | nullable |
| `usage_limit` | PositiveInteger | nullable |
| `used_count` | PositiveInteger | not editable |
| `is_active` | Boolean |  |

#### `Discount`

A campaign: this much off these things, between these dates.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `name` | Char |  |
| `description` | Text |  |
| `kind` | Char |  |
| `value` | Decimal |  |
| `max_amount` | Decimal | nullable |
| `starts_at` | DateTime | nullable |
| `ends_at` | DateTime | nullable |
| `is_active` | Boolean |  |
| `priority` | Integer |  |
| `applies_to_all` | Boolean |  |

#### `InventoryReservation`

Stock this order has taken off the shelf, and whether it was put back.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `order` | ForeignKey | → `shop.Order` |
| `product` | ForeignKey | → `shop.Product` |
| `variant` | ForeignKey | → `shop.ProductVariant`, nullable |
| `offer` | ForeignKey | → `shop.ProductOffer`, nullable |
| `quantity` | PositiveInteger |  |
| `released_at` | DateTime | nullable |

#### `Invoice`

The document that says what was owed, issued when the order is placed.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `order` | OneToOne | unique, → `shop.Order` |
| `number` | Char | unique, not editable |
| `issued_at` | DateTime |  |
| `due_at` | DateTime | nullable |
| `notes` | Text |  |

#### `Order`

What somebody agreed to buy, at the prices they agreed to.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `user` | ForeignKey | → `accounts.User` |
| `number` | Char | unique, not editable |
| `status` | Char |  |
| `currency` | Char |  |
| `shipping_address` | JSON |  |
| `shipping_method` | ForeignKey | → `shop.ShippingMethod`, nullable |
| `coupon` | ForeignKey | → `shop.Coupon`, nullable |
| `subtotal` | Decimal |  |
| `coupon_discount` | Decimal |  |
| `shipping_total` | Decimal |  |
| `tax_total` | Decimal |  |
| `total` | Decimal |  |
| `note` | Text |  |
| `carrier` | Char |  |
| `tracking_number` | Char |  |
| `tracking_url` | Char |  |
| `shipped_at` | DateTime | not editable, nullable |

#### `OrderEvent`

One thing that happened to one order, in the order it happened.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `order` | ForeignKey | → `shop.Order` |
| `status` | Char |  |
| `note` | Char |  |
| `actor` | ForeignKey | → `accounts.User`, nullable |

#### `OrderItem`

One line of an order, carrying its own copy of what was sold.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `order` | ForeignKey | → `shop.Order` |
| `product` | ForeignKey | → `shop.Product`, nullable |
| `variant` | ForeignKey | → `shop.ProductVariant`, nullable |
| `offer` | ForeignKey | → `shop.ProductOffer`, nullable |
| `seller` | ForeignKey | → `shop.Seller`, nullable |
| `product_name` | Char |  |
| `seller_name` | Char |  |
| `sku` | Char |  |
| `quantity` | PositiveInteger |  |
| `unit_price` | Decimal |  |
| `tax_rate` | Decimal |  |
| `line_total` | Decimal |  |

#### `Payment`

One attempt to collect what an order is worth.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `order` | ForeignKey | → `shop.Order` |
| `provider` | Char |  |
| `status` | Char |  |
| `amount` | Decimal |  |
| `currency` | Char |  |
| `idempotency_key` | UUID | unique, not editable |
| `provider_reference` | Char |  |
| `paid_at` | DateTime | nullable |

#### `Product`

One thing a shop sells.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `category` | ForeignKey | → `shop.Category` |
| `brand` | ForeignKey | → `shop.Brand`, nullable |
| `seller` | ForeignKey | → `shop.Seller`, nullable |
| `name` | Char |  |
| `slug` | Slug | unique |
| `subtitle` | Char |  |
| `summary` | Text |  |
| `description` | Text |  |
| `sku` | Char | unique |
| `barcode` | Char |  |
| `price` | Decimal |  |
| `compare_at_price` | Decimal | nullable |
| `cost_price` | Decimal | nullable |
| `tax_rate` | Decimal |  |
| `status` | Char |  |
| `published_at` | DateTime | nullable |
| `is_featured` | Boolean |  |
| `has_variants` | Boolean |  |
| `track_inventory` | Boolean |  |
| `stock` | Integer |  |
| `low_stock_threshold` | PositiveInteger |  |
| `allow_backorder` | Boolean |  |
| `weight_grams` | PositiveInteger | nullable |
| `length_mm` | PositiveInteger | nullable |
| `width_mm` | PositiveInteger | nullable |
| `height_mm` | PositiveInteger | nullable |
| `meta_title` | Char |  |
| `meta_description` | Char |  |
| `meta_keywords` | JSON |  |
| `rating_average` | Decimal | not editable |
| `rating_count` | PositiveInteger | not editable |
| `like_count` | PositiveInteger | not editable |
| `sales_count` | PositiveInteger |  |
| `view_count` | PositiveInteger | not editable |
| `order` | PositiveInteger |  |

#### `ProductAttribute`

One product's answer to one of its category's attributes.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `product` | ForeignKey | → `shop.Product` |
| `attribute` | ForeignKey | → `shop.CategoryAttribute` |
| `value` | JSON |  |

#### `ProductImage`

One picture of one product.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `product` | ForeignKey | → `shop.Product` |
| `url` | Char |  |
| `alt` | Char |  |
| `caption` | Char |  |
| `is_primary` | Boolean |  |
| `order` | PositiveInteger |  |

#### `ProductLike`

One account having marked one product. The row's existence is the like.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `product` | ForeignKey | → `shop.Product` |
| `user` | ForeignKey | → `accounts.User` |

#### `ProductOffer`

One seller's price and stock for one thing.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `product` | ForeignKey | → `shop.Product` |
| `seller` | ForeignKey | → `shop.Seller` |
| `variant` | ForeignKey | → `shop.ProductVariant`, nullable |
| `sku` | Char |  |
| `price` | Decimal |  |
| `stock` | Integer |  |
| `condition` | Char |  |
| `lead_time_days` | PositiveSmallInteger |  |
| `is_active` | Boolean |  |

#### `ProductVariant`

One buyable version of a product: this size, in this colour.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `is_active` | Boolean |  |
| `product` | ForeignKey | → `shop.Product` |
| `name` | Char |  |
| `sku` | Char | unique |
| `options` | JSON |  |
| `price` | Decimal | nullable |
| `stock` | Integer |  |
| `image` | Char |  |
| `order` | PositiveInteger |  |

#### `Review`

What one account thought of one product. One review each, editable.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `product` | ForeignKey | → `shop.Product` |
| `user` | ForeignKey | → `accounts.User` |
| `rating` | PositiveSmallInteger |  |
| `title` | Char |  |
| `body` | Text |  |
| `status` | Char |  |
| `moderator_note` | Char |  |

#### `Seller`

A shop within the shop: whoever is actually selling the thing.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `is_active` | Boolean |  |
| `name` | Char | unique |
| `slug` | Slug | unique |
| `description` | Text |  |
| `logo` | Char |  |
| `email` | Char |  |
| `phone` | Char |  |
| `city` | Char |  |
| `owner` | ForeignKey | → `accounts.User`, nullable |
| `order` | PositiveInteger |  |

#### `ShippingMethod`

How it gets there, what that costs, and how long it takes.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `name` | Char | unique |
| `description` | Text |  |
| `price` | Decimal |  |
| `free_from` | Decimal | nullable |
| `min_days` | PositiveSmallInteger |  |
| `max_days` | PositiveSmallInteger |  |
| `is_active` | Boolean |  |
| `order` | PositiveInteger |  |

#### `Tag`

A free label on a product: "vegan", "refurbished", "bestseller-2026".

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID | primary key, not editable |
| `created_at` | DateTime | not editable |
| `updated_at` | DateTime | not editable |
| `name` | Char | unique |
| `slug` | Slug | unique |
<!-- /generated:models -->

## Admin

<!-- generated:admin -->
| Model | Editable | Actions | Columns |
| --- | --- | --- | --- |
| `Address` | No — read-only | — | `full_name`, `account`, `city`, `country`, `postal_code`, `default_badge` |
| `Brand` | Yes | — | `name`, `product_count`, `is_active`, `order` |
| `Cart` | No — read-only | — | `user`, `line_count`, `unit_count`, `updated_at` |
| `Category` | Yes | — | `tree_name`, `attribute_count`, `product_count`, `is_active`, `order` |
| `CategoryAttribute` | Yes | — | `name`, `category`, `attribute_type`, `required`, `is_variant`, `order` |
| `Collection` | Yes | — | `name`, `product_count`, `is_active`, `order` |
| `Coupon` | Yes | — | `code`, `worth`, `minimum_subtotal`, `usage`, `window`, `state` |
| `Discount` | Yes | — | `name`, `offer`, `window`, `running`, `priority`, `is_active` |
| `InventoryReservation` | No — read-only | — | `product`, `variant`, `seller`, `quantity`, `order_link`, `state` |
| `Invoice` | Yes | — | `number`, `order`, `customer`, `total`, `order_status`, `issued_at` |
| `Order` | Yes | `mark_paid`, `start_processing`, `mark_sent`, `mark_completed`, `cancel_orders`, `refund_orders` | `number`, `customer`, `status_badge`, `line_count`, `money`, `fulfilment`, `invoice_number`, `created_at` |
| `OrderEvent` | No — read-only | — | `created_at`, `order_number`, `status_badge`, `note`, `actor` |
| `Payment` | No — read-only | `mark_paid`, `mark_rejected` | `order`, `provider`, `status_badge`, `amount`, `currency`, `provider_reference`, `paid_at` |
| `Product` | Yes | `publish`, `unpublish`, `archive`, `feature`, `unfeature` | `name`, `category`, `brand`, `seller`, `live_price`, `stock_state`, `rating_badge`, `like_count`, `status_badge` |
| `ProductLike` | No — read-only | — | `product`, `user`, `created_at` |
| `ProductOffer` | Yes | — | `product`, `seller`, `variant`, `live_price`, `stock_state`, `condition`, `is_active` |
| `ProductVariant` | Yes | — | `sku`, `product`, `label`, `price`, `stock`, `is_active` |
| `Review` | Yes | `approve`, `reject` | `product`, `author`, `stars`, `title`, `status_badge`, `created_at` |
| `Seller` | Yes | — | `name`, `city`, `listing_count`, `offer_count`, `is_active`, `order` |
| `ShippingMethod` | Yes | — | `name`, `cost_summary`, `speed`, `is_active`, `order` |
| `Tag` | Yes | — | `name`, `slug`, `product_count` |
<!-- /generated:admin -->

The catalogue is built here, so these screens are not a convenience — they are
the product's only editing surface. Every number a shopkeeper glances at is on
the list screen: the price and what a campaign currently makes of it, stock and
whether it is running low, the rating and how many people left one. The queries
behind those columns are annotated once for the page rather than run per row.

A product's form offers exactly the attributes its category declared, and says
out loud which required ones are still empty — the one place the `required` flag
is enforced, because it is the only place somebody is in a position to answer it.
Other sellers' offers are edited on the product, since "who else sells this, and
for how much" is a question asked while looking at the thing.

Records of what shoppers did — baskets, likes, payments — are read-only.
Deletion stays available, because a shop that has to remove somebody's data on
request needs a way to.

## Setup

<!-- generated:settings -->
| Environment variable | Required | Purpose |
| --- | --- | --- |
| `DJANGO_SHOP_ENABLED` | **Yes** | Installs the app, its migrations, its routes and its admin. Unset, a project carries no shop at all. |
| `DJANGO_SHOP_CURRENCY` | Optional | The ISO 4217 code every price is quoted in. Defaults to `USD`. |
| `DJANGO_SHOP_REVIEW_MODERATION` | Optional | Whether a review waits for a moderator before anybody can read it. Defaults to on. |
| `DJANGO_SHOP_MAX_ITEM_QUANTITY` | Optional | The most of one product a single cart line may hold. Defaults to 99. |
| `DJANGO_SHOP_PAGE_SIZE` | Optional | How many products a listing returns when the caller does not say. Defaults to 24. |
| `DJANGO_SHOP_MAX_PAGE_SIZE` | Optional | The ceiling on `limit`, so one request cannot ask for the catalogue. Defaults to 100. |
<!-- /generated:settings -->

```bash
DJANGO_SHOP_ENABLED=true
DJANGO_SHOP_CURRENCY=GBP                # one code for the whole shop
DJANGO_SHOP_REVIEW_MODERATION=true      # hold a review for a moderator
DJANGO_SHOP_MAX_ITEM_QUANTITY=99        # per basket line
DJANGO_SHOP_PAGE_SIZE=24                # when a caller does not say
DJANGO_SHOP_MAX_PAGE_SIZE=100           # the ceiling on `limit`
```

Only the first line is needed. One currency for the whole shop rather than one
per product, because a catalogue priced in several needs a conversion policy, a
rounding policy and a display policy, and inventing those silently is worse than
saying a shop has one currency.

Then `manage.py migrate`, and build a catalogue in the admin: a category with
its attributes, a brand, a seller, and a product that answers them.

## Using it

The whole storefront, over HTTP:

```bash
curl 'http://localhost:8000/api/v1/shop/categories'
curl 'http://localhost:8000/api/v1/shop/products?category=laptops&attribute=screen-size:14&sort=price_low'
curl 'http://localhost:8000/api/v1/shop/products/featherbook-14'
curl 'http://localhost:8000/api/v1/shop/listings/bestsellers'
```

Filling a basket and buying from it needs a credential:

```bash
TOKEN=... # from any of this project's login methods

curl -X POST 'http://localhost:8000/api/v1/shop/cart/items' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"product": "featherbook-14", "quantity": 1}'

curl -X POST 'http://localhost:8000/api/v1/shop/checkout' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"address": "<address id>", "shipping_method": "<method id>", "coupon": "WELCOME"}'

curl 'http://localhost:8000/api/v1/shop/orders/S20260902ABCD1234/invoice' \
  -H "Authorization: Bearer $TOKEN"
```

The same shop answers over **GraphQL** at `/graphql` (`shopProducts`,
`shopProduct`, `shopCart`, `shopOrders`, `shopInvoice`, and the basket, address,
review, like and order mutations) and over **gRPC** on `DJANGO_GRPC_PORT`
(`ShopController`). All three call the same service, so a decision — what
"bestsellers" means, whether an unmoderated review is visible, which seller wins
the buy box — is made once and cannot drift between doors. The gRPC basket and
order calls read the caller from `authorization` metadata, so no field on any
request could name somebody else's.

**All three doors carry the whole shop.** Every route above has its counterpart
on the other two: the catalogue and each of its filters, the category tree,
brands, collections, the product page, related products, view counts, reviews,
likes, the basket, the address book, checkout, orders, invoices and the payment
callback. A door that answered only part of it would push its clients back onto
HTTP for the rest — and the half nobody exercised is the half that rots.

Three differences between the doors are the transport's rather than the shop's,
and protobuf forces all three. Money crosses the wire as a string, because
protobuf has no decimal and a shop that adds up nearly prices sells things for
nearly the right amount; that is also why an empty `min_price` means "no bound"
rather than zero. An attribute's value and a variant's options cross as JSON,
because the shape of each is decided by a type the same message carries. And
`UpdateAddress` reads an empty field as "not sent", so it cannot *clear* an
optional one — the HTTP `PATCH` can tell those two apart. `is_default` is
deliberately not on that request for the same reason: `SetDefaultAddress` is how
the default gets chosen, because a bool indistinguishable from unset would
silently unset it on every unrelated edit.
