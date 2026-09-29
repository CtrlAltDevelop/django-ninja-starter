"""How often one caller may ask, whichever endpoint they are asking.

Authentication says *who* may call something; this says *how often*, and the two
answer different attacks. A correct credential is not a licence to make a
hundred thousand requests, and a public catalogue that authenticates nobody is
the endpoint most worth scraping.

Two scopes cover nearly everything, because Django Ninja resolves the caller
before it checks a throttle, so by this point a request either proved an account
or did not:

* **anon** counts by IP and only applies where nothing was proved. This is the
  limit on a public catalogue, a sitemap and a login form.
* **auth** counts by credential and applies everywhere else, so one account's
  traffic is one account's problem rather than the shared address's -- an office
  behind one NAT would otherwise throttle itself.

Two narrower ones exist because the default cannot be tight enough for them:

* **login** is for the endpoints that take a secret and say whether it was
  right. They are rate-limited per destination already -- see
  :mod:`infrastructure.auth.core.throttling` -- but that counts guesses against
  *one* account, and a credential-stuffing run makes one guess against each of
  fifty thousand. That shape is invisible per destination and obvious per IP.
* **upload** is for the endpoints that write a file to storage, where the cost
  of a request is a disk rather than a query.

**The counters live in Django's default cache**, so what a deployment gets
depends on what it configured there. The default is per process, which means a
project running four workers hands out four times the limit; anything that runs
Redis or Memcached gets one shared limit across the fleet, which is the point.
A rate limit is a load-shedding measure rather than an authorisation control:
this is the cheap layer that keeps honest traffic honest, not the one that stops
a determined attacker, and a deployment that needs the latter puts a WAF or its
CDN in front.

Every rate is a setting, and setting one empty turns that scope off. A limit
nobody can raise is a limit somebody disables entirely at 3am.

**The rate is read per request, not when the throttle is built.** Django Ninja
constructs these once, while the API is being assembled at import, so a rate
captured there would be frozen for the life of the process -- and
``override_settings`` in somebody's test would quietly do nothing, which is the
kind of silence that gets a limit shipped at the wrong number.
"""

from django.conf import settings
from django.http import HttpRequest
from ninja.throttling import AnonRateThrottle, AuthRateThrottle, BaseThrottle


def rate_for(scope: str) -> str:
    """The configured rate for one scope, or ``""`` where it is turned off."""
    return str(getattr(settings, f"API_THROTTLE_{scope.upper()}", "") or "")


#: What a throttle is built with before it has read the settings. Never used to
#: decide anything: `allow_request` reloads the real rate first, every time.
PLACEHOLDER_RATE = "1/s"


class ScopedAnonThrottle(AnonRateThrottle):
    """Counts unauthenticated requests by IP, under a scope of its own.

    The rate is reloaded per request rather than parsed once in ``__init__``.
    These are built while the API is assembled at import, so a rate captured
    there would be frozen for the life of the process -- and ``override_settings``
    in somebody's test would quietly do nothing, which is the kind of silence
    that gets a limit shipped at the wrong number.
    """

    def __init__(self, scope: str) -> None:
        self.scope = scope
        super().__init__(rate=rate_for(scope) or PLACEHOLDER_RATE)

    def allow_request(self, request: HttpRequest) -> bool:
        rate = rate_for(self.scope)
        if not rate:
            return True
        self.rate = rate
        self.num_requests, self.duration = self.parse_rate(rate)
        return bool(super().allow_request(request))


class ScopedAuthThrottle(AuthRateThrottle):
    """Counts authenticated requests by credential, under a scope of its own.

    Only authenticated ones. Django Ninja's own ``AuthRateThrottle`` falls back
    to the IP where no credential was proved, which would count every anonymous
    caller under *both* scopes -- so the anonymous limit could never be raised
    above the authenticated one, and turning it off would free nobody. Skipping
    here is what makes the two dials independent and each one mean what it says.
    """

    def __init__(self, scope: str) -> None:
        self.scope = scope
        super().__init__(rate=rate_for(scope) or PLACEHOLDER_RATE)

    def allow_request(self, request: HttpRequest) -> bool:
        rate = rate_for(self.scope)
        if not rate or getattr(request, "auth", None) is None:
            return True
        self.rate = rate
        self.num_requests, self.duration = self.parse_rate(rate)
        return bool(super().allow_request(request))


def default_throttles() -> list[BaseThrottle]:
    """The pair every endpoint gets unless it asks for something tighter.

    Both are attached rather than one chosen, because which applies is decided
    per request rather than per route: the same product page is read by a
    signed-out shopper and a signed-in one, and only the first should be counted
    against the address they happen to share with an office.

    Attached whatever the settings currently say, because the rate is read per
    request -- a scope turned off is a throttle that allows everything, not a
    throttle that is missing and cannot be turned back on without a restart.
    """
    return [ScopedAnonThrottle("anon"), ScopedAuthThrottle("auth")]


def login_throttles() -> list[BaseThrottle]:
    """For the endpoints that take a secret and say whether it was right.

    Counted by IP rather than by account, which is the axis the per-destination
    limits in the authentication core cannot see: fifty thousand accounts tried
    once each never trips a per-account counter, and is the attack people
    actually run.

    Its own scope so a deployment can loosen its ordinary API without loosening
    this, which is the pair of numbers most worth moving independently.
    """
    return [ScopedAnonThrottle("login")]


def upload_throttles() -> list[BaseThrottle]:
    """For the endpoints whose cost is a file on a disk rather than a query."""
    return [ScopedAuthThrottle("upload")]
