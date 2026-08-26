"""Let a login enrich the profile without ever overwriting the person's own words.

A social provider knows a display name and an avatar; an email code knows that
the address it reached is real. Both are worth recording on the account, and
neither is a reason to overwrite something the user set themselves.

Written defensively on purpose. A project that swaps ``AUTH_USER_MODEL`` for its
own model without a ``profile`` relation should lose this enrichment, not its
ability to log people in.
"""

from typing import Any


def enrich_profile(user: Any, *, display_name: str = "", avatar_url: str = "") -> list[str]:
    """Fill any still-empty profile fields from what a login just learned."""
    profile = getattr(user, "profile", None)
    if profile is None or not hasattr(profile, "fill_blanks"):
        return []
    return list(profile.fill_blanks(display_name=display_name, avatar_url=avatar_url))


def confirm_email(user: Any, email: str) -> bool:
    """Record that an address was reached, when it is the one on the account.

    Redeeming a code or a link proves the address works. Marking that only when
    it matches the account's own address keeps the flag meaning what it says: a
    login to a *different* address is a fact about that address, not this one.
    """
    marker = getattr(user, "mark_email_verified", None)
    if marker is None or not email:
        return False
    current = (getattr(user, "email", "") or "").lower()
    if current != email.lower() or getattr(user, "email_verified_at", None) is not None:
        return False
    marker()
    return True
