"""Links that show a draft to somebody who is not signed in.

An editor's first question about a draft is "can I see it?", and their second is
"can I show it to somebody?" -- usually somebody who has no admin account and is
reading on their phone. Requiring a login answers neither.

So a preview link is a signed token rather than a session: it names one page,
it expires, and it is verified with the project's own ``SECRET_KEY``. Nothing
about it is guessable, nothing about it grants anything else, and a leaked one
stops working on its own.
"""

from django.conf import settings
from django.core import signing

SALT = "apps.cms.preview"


def token_ttl() -> int:
    """How long a preview link lasts. Defaulted, so the app needs no settings."""
    return int(getattr(settings, "CMS_PREVIEW_TTL_SECONDS", 60 * 60 * 24))


def make_token(slug: str) -> str:
    """A token that says "this page, for a while"."""
    return signing.dumps(slug, salt=SALT)


def preview_url(page: object) -> str:
    """The link an editor copies. Relative, so it works wherever this is served."""
    slug = getattr(page, "slug", "")
    return f"/api/v1/cms/pages/{slug}?preview={make_token(slug)}"


def slug_from_token(token: str) -> str | None:
    """The page the token names, or ``None`` if it is stale, forged or truncated."""
    try:
        slug = signing.loads(token, salt=SALT, max_age=token_ttl())
    except signing.BadSignature:
        return None
    return slug if isinstance(slug, str) else None
