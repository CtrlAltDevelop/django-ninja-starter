"""The HTTP transport for second factors.

Two routers, one prefix: `login.py` carries the challenge and the verification
that finish a sign-in already in progress, `api.py` carries enrolment for an
account that is already signed in. `router` is what the project mounts.
"""

from ninja import Router

from infrastructure.auth.twofactor.rest.api import router as enrolment_router
from infrastructure.auth.twofactor.rest.login import router as login_router

router = Router()
router.add_router("", login_router)
router.add_router("", enrolment_router)

__all__ = ["enrolment_router", "login_router", "router"]
