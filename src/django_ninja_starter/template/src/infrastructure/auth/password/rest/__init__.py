"""The HTTP transport for password authentication.

Two routers, one prefix: `login` carries the ways in and out, `api` carries
everything an account does to its own password afterwards. `router` is what the
project mounts, and it is the pair of them together.
"""

from ninja import Router

from infrastructure.auth.password.rest.api import router as account_router
from infrastructure.auth.password.rest.login import router as login_router

router = Router()
router.add_router("", login_router)
router.add_router("", account_router)

__all__ = ["account_router", "login_router", "router"]
