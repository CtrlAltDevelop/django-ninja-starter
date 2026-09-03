"""One shape per thing, built once and served over all three transports.

REST, GraphQL and gRPC each publish this catalogue, and each of them would
otherwise grow its own idea of what a product looks like -- three places to
change when an attribute is added, and three chances to disagree about whether
the price is the discounted one. So the shape is decided here, in plain
dictionaries, and every transport is a translation of these into its own
vocabulary.

A dictionary rather than the model itself, for the reason a shop finds out about
eventually: a model has ``cost_price`` on it. Serialising the row means every
new column is published by default and a private one is published by accident.
Building the payload by hand means a field is public because somebody wrote it
down here.

The querysets at the bottom belong with the payloads for the same reason: what
a payload reads is exactly what has to be prefetched, and keeping them apart is
how a listing of forty products becomes four hundred queries.
"""

from typing import Any

from django.db.models import Prefetch, QuerySet

from apps.shop import options
from apps.shop.models import (
    Brand,
    Cart,
    CartItem,
    Category,
    CategoryAttribute,
    Collection,
    Product,
    ProductAttribute,
    ProductImage,
    ProductOffer,
    ProductVariant,
    Review,
    Seller,
)
from apps.shop.pricing import Price


def brand_payload(brand: Brand) -> dict[str, Any]:
    return {
        "id": brand.slug,
        "name": brand.name,
        "slug": brand.slug,
        "description": brand.description,
        "logo": brand.logo,
        "website": brand.website,
    }


def seller_payload(seller: Seller) -> dict[str, Any]:
    """One seller, as a storefront names them. Never their owner's account."""
    return {
        "id": seller.slug,
        "name": seller.name,
        "slug": seller.slug,
        "description": seller.description,
        "logo": seller.logo,
        "city": seller.city,
    }


def offer_payload(offer: ProductOffer, price: Price) -> dict[str, Any]:
    """One seller's offer of one thing, priced now.

    The seller is nested rather than named, because a "sold by" line is a link
    and a client that has only the name has to go and look the rest up.
    """
    return {
        "id": str(offer.pk),
        "seller": seller_payload(offer.seller),
        "variant": str(offer.variant_id) if offer.variant_id else None,
        "sku": offer.sku,
        "condition": str(offer.condition),
        "lead_time_days": offer.lead_time_days,
        "stock": offer.stock,
        "in_stock": offer.in_stock,
        "price": price.payload(),
    }


def attribute_payload(attribute: CategoryAttribute) -> dict[str, Any]:
    """One attribute of a category: what a product here answers, and how."""
    return {
        "code": attribute.code,
        "name": attribute.name,
        "type": str(attribute.attribute_type),
        "unit": attribute.unit,
        "choices": list(attribute.choices),
        "required": attribute.required,
        "is_variant": attribute.is_variant,
        "is_filterable": attribute.is_filterable,
        "help_text": attribute.help_text,
    }


def category_summary(category: Category, *, product_count: int | None = None) -> dict[str, Any]:
    """A row in a category list. ``id`` is the slug the detail route takes."""
    return {
        "id": category.slug,
        "name": category.name,
        "slug": category.slug,
        "description": category.description,
        "image": category.image,
        "icon": category.icon,
        "parent": category.parent.slug if category.parent_id else None,
        "product_count": product_count,
    }


def category_tree(
    categories: list[Category], counts: dict[Any, int] | None = None
) -> list[dict[str, Any]]:
    """The whole tree, nested, from one flat list of rows.

    Assembled in Python rather than with a recursive query: the rows are already
    loaded, a shop has tens of categories, and a database-portable recursive CTE
    is a great deal of machinery for a list that fits on a screen.
    """
    # `None` means nobody asked for counts; a category with none of its own
    # counts zero rather than reading as "unknown".
    nodes = {
        category.pk: {
            **category_summary(
                category,
                product_count=None if counts is None else counts.get(category.pk, 0),
            ),
            "children": [],
        }
        for category in categories
    }
    roots: list[dict[str, Any]] = []
    for category in categories:
        node = nodes[category.pk]
        parent = nodes.get(category.parent_id) if category.parent_id else None
        if parent is None:
            roots.append(node)
        else:
            parent["children"].append(node)
    return roots


def category_payload(category: Category, *, product_count: int | None = None) -> dict[str, Any]:
    """One category: what it is, where it sits, and the shape of what is in it."""
    return {
        **category_summary(category, product_count=product_count),
        "breadcrumbs": [
            {"name": ancestor.name, "slug": ancestor.slug}
            for ancestor in category.ancestors(including_self=True)
        ],
        "children": [
            category_summary(child) for child in category.children.all() if child.is_active
        ],
        "attributes": [attribute_payload(attribute) for attribute in category.attribute_schema()],
        "meta": {
            "title": category.meta_title or category.name,
            "description": category.meta_description,
            "keywords": list(category.meta_keywords),
        },
    }


def image_payload(image: ProductImage) -> dict[str, Any]:
    return {
        "url": image.url,
        "alt": image.alt,
        "caption": image.caption,
        "is_primary": image.is_primary,
    }


def _primary_image(product: Product) -> str:
    """The picture a card shows.

    The image ordering puts the primary one first, so this is the first row
    rather than a second query looking for ``is_primary``.
    """
    images = list(product.images.all())
    return images[0].url if images else ""


def variant_payload(variant: ProductVariant, price: Price) -> dict[str, Any]:
    return {
        "id": str(variant.pk),
        "sku": variant.sku,
        "label": variant.label,
        "options": dict(variant.options),
        "image": variant.image,
        "stock": variant.stock,
        "in_stock": variant.stock > 0,
        "price": price.payload(),
    }


def product_attribute_payload(value: ProductAttribute) -> dict[str, Any]:
    """One answered attribute, carrying enough to print a spec row without a lookup."""
    return {
        "code": value.attribute.code,
        "name": value.attribute.name,
        "type": str(value.attribute.attribute_type),
        "unit": value.attribute.unit,
        "value": value.value,
    }


def product_summary(product: Product, price: Price) -> dict[str, Any]:
    """A card: what a listing, a search result and a related-products row need.

    Deliberately not the detail payload with fields removed. A listing of forty
    products should not be loading forty descriptions, forty attribute tables
    and forty variant lists, and the only way to be sure it is not is for the
    summary to be its own shape.
    """
    return {
        "id": product.slug,
        "slug": product.slug,
        "name": product.name,
        "subtitle": product.subtitle,
        "summary": product.summary,
        "image": _primary_image(product),
        "brand": product.brand.name if product.brand_id else None,
        "brand_slug": product.brand.slug if product.brand_id else None,
        "seller": product.seller.name if product.seller_id else None,
        "seller_slug": product.seller.slug if product.seller_id else None,
        "category": product.category.slug,
        "category_name": product.category.name,
        "price": price.payload(),
        "compare_at_price": product.compare_at_price,
        "in_stock": product.in_stock,
        "has_variants": product.has_variants,
        "is_featured": product.is_featured,
        "rating_average": product.rating_average,
        "rating_count": product.rating_count,
        "like_count": product.like_count,
        "sales_count": product.sales_count,
        "tags": [tag.slug for tag in product.tags.all()],
        "created_at": product.created_at,
    }


def product_payload(
    product: Product,
    price: Price,
    *,
    variant_prices: dict[Any, Price] | None = None,
    liked: bool = False,
    own_review: Review | None = None,
    offers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Everything on a product page, in one answer.

    ``liked`` and ``own_review`` are the caller's own relationship to the
    product, and they are here rather than in a second request because a page
    that has to make one is a page that renders the heart empty and then fills
    it in.
    """
    prices = variant_prices or {}
    return {
        **product_summary(product, price),
        "sold_by": seller_payload(product.seller) if product.seller_id else None,
        "description": product.description,
        "sku": product.sku,
        "barcode": product.barcode,
        "tax_rate": product.tax_rate,
        "status": str(product.status),
        "published_at": product.published_at,
        "stock": product.available_stock if product.track_inventory else None,
        "track_inventory": product.track_inventory,
        "allow_backorder": product.allow_backorder,
        "weight_grams": product.weight_grams,
        "dimensions_mm": (
            {
                "length": product.length_mm,
                "width": product.width_mm,
                "height": product.height_mm,
            }
            if any((product.length_mm, product.width_mm, product.height_mm))
            else None
        ),
        "images": [image_payload(image) for image in product.images.all()],
        "attributes": [
            product_attribute_payload(value) for value in product.attribute_values.all()
        ],
        "variant_attributes": [
            attribute_payload(attribute)
            for attribute in product.category.attribute_schema()
            if attribute.is_variant
        ],
        "variants": [
            variant_payload(variant, prices.get(variant.pk) or price)
            for variant in product.variants.all()
            if variant.is_active
        ],
        "breadcrumbs": [
            {"name": ancestor.name, "slug": ancestor.slug}
            for ancestor in product.category.ancestors(including_self=True)
        ],
        "meta": {
            "title": product.meta_title or product.name,
            "description": product.meta_description or product.summary,
            "keywords": list(product.meta_keywords),
        },
        "liked": liked,
        "own_review": review_payload(own_review) if own_review else None,
        "offers": offers if offers is not None else [],
        "seller_count": (1 if product.seller_id else 0) + len(offers or []),
    }


def collection_payload(
    collection: Collection, products: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "id": collection.slug,
        "name": collection.name,
        "slug": collection.slug,
        "description": collection.description,
        "image": collection.image,
        "products": products if products is not None else [],
    }


def review_payload(review: Review) -> dict[str, Any]:
    """One review. The author is a display name, never an address.

    Reviews are the most-read page on a shop and the one most often scraped, and
    an email address published beside a name is a mailing list somebody else now
    owns.
    """
    return {
        "id": str(review.pk),
        "product": review.product.slug if review.product_id else None,
        "author": review.user.get_username() if review.user_id else "",
        "rating": review.rating,
        "title": review.title,
        "body": review.body,
        "status": str(review.status),
        "created_at": review.created_at,
        "updated_at": review.updated_at,
    }


def _line_seller(item: CartItem) -> str | None:
    """Who this line is being bought from, as a name a basket can print."""
    if item.offer_id:
        return item.offer.seller.name
    return item.product.seller.name if item.product.seller_id else None


def _line_is_fillable(item: CartItem) -> bool:
    """Whether whoever is selling this line still has enough of it.

    Which shelf to look at follows the same rule the price does: the offer's if
    the line names one, then the variant's, then the product's.
    """
    if item.offer_id:
        return item.offer.stock >= item.quantity or item.product.allow_backorder
    if item.variant_id:
        return item.variant.stock >= item.quantity
    return not item.product.track_inventory or item.product.in_stock


def cart_line_payload(item: CartItem, price: Price) -> dict[str, Any]:
    """One basket line, priced now rather than when it was added."""
    line_total = price.amount * item.quantity
    return {
        "id": str(item.pk),
        "product": item.product.slug,
        "name": item.product.name,
        "image": _primary_image(item.product),
        "variant": str(item.variant_id) if item.variant_id else None,
        "variant_label": item.variant.label if item.variant_id else None,
        "offer": str(item.offer_id) if item.offer_id else None,
        "seller": _line_seller(item),
        "quantity": item.quantity,
        "unit_price": price.payload(),
        "line_total": line_total,
        "in_stock": _line_is_fillable(item),
    }


def cart_payload(cart: Cart, lines: list[dict[str, Any]]) -> dict[str, Any]:
    """The basket, with the three totals every checkout page prints.

    ``discount_total`` is stated rather than left to be worked out, because "you
    saved this much" is the sentence the shop wants on the page and a client
    subtracting two numbers to get it will eventually subtract them in the wrong
    order.
    """
    subtotal = sum((line["unit_price"]["base_amount"] * line["quantity"] for line in lines), 0)
    total = sum((line["line_total"] for line in lines), 0)
    return {
        "id": str(cart.pk),
        "currency": options.currency(),
        "items": lines,
        "item_count": len(lines),
        "unit_count": sum(line["quantity"] for line in lines),
        "subtotal": subtotal,
        "discount_total": subtotal - total,
        "total": total,
        "updated_at": cart.updated_at,
    }


def order_line_payload(item: Any) -> dict[str, Any]:
    """One line of an order, read back off the snapshot rather than the catalogue."""
    return {
        "product": item.product.slug if item.product_id else None,
        "name": item.product_name,
        "seller": item.seller_name or None,
        "sku": item.sku,
        "quantity": item.quantity,
        "unit_price": item.unit_price,
        "tax_rate": item.tax_rate,
        "line_total": item.line_total,
    }


def order_payload(order: Any) -> dict[str, Any]:
    """One order: what was bought, what it cost, and where it stands.

    Everything here is read off the order's own columns. A payload that went
    back to the catalogue for a name or a price would answer today's question
    about last month's purchase.
    """
    payment = order.payments.order_by("-created_at").first()
    return {
        "number": order.number,
        "status": str(order.status),
        "status_label": order.get_status_display(),
        "currency": order.currency,
        "placed_at": order.created_at,
        "items": [order_line_payload(item) for item in order.items.all()],
        "subtotal": order.subtotal,
        "coupon": order.coupon.code if order.coupon_id else None,
        "coupon_discount": order.coupon_discount,
        "shipping_method": order.shipping_method.name if order.shipping_method_id else None,
        "shipping_total": order.shipping_total,
        "tax_total": order.tax_total,
        "total": order.total,
        "shipping_address": dict(order.shipping_address),
        "note": order.note,
        "payment_status": str(payment.status) if payment else None,
        "invoice": getattr(getattr(order, "invoice", None), "number", None),
    }


def invoice_payload(invoice: Any) -> dict[str, Any]:
    """The document, and the order it is a demand for payment against.

    The order is nested whole rather than summarised: an invoice that did not
    carry its own lines and totals would be a number a client has to go and
    resolve before it can print anything.
    """
    return {
        "number": invoice.number,
        "issued_at": invoice.issued_at,
        "due_at": invoice.due_at,
        "notes": invoice.notes,
        "order": order_payload(invoice.order),
    }


def listed_products() -> QuerySet[Product]:
    """The queryset every product *listing* starts from.

    Exactly the relations ``product_summary`` reads, and no more: the brand and
    category it names, the one image it shows, the tags it lists.
    """
    return Product.objects.select_related("brand", "category", "seller").prefetch_related(
        "tags",
        Prefetch("images", queryset=ProductImage.objects.order_by("-is_primary", "order")),
        # The buy box compares the product's own price against every offer, so a
        # listing that did not load these would ask the database once per row.
        Prefetch("offers", queryset=ProductOffer.objects.select_related("seller")),
    )


def detailed_products() -> QuerySet[Product]:
    """The queryset a product *page* starts from: everything on it, in five queries."""
    return listed_products().prefetch_related(
        "variants",
        Prefetch(
            "attribute_values",
            queryset=ProductAttribute.objects.select_related("attribute").order_by(
                "attribute__order", "attribute__name"
            ),
        ),
        "category__attributes",
        "category__parent__attributes",
    )
