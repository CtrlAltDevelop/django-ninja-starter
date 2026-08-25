"""A URLConf mounting the session mode's token router, for tests to point at.

The project's own API is assembled once at import time from the configured token
mode, so a test cannot mount a different mode's router by overriding a setting.
It gets its own URLConf instead, wired the same way ``config.api`` wires the real
one: a login method to obtain a credential, and the mode under test at the prefix
clients actually use.
"""

from django.urls import path
from ninja import NinjaAPI

from infrastructure.auth.core.errors import register_auth_exception_handlers
from infrastructure.auth.password.api import router as password_router
from infrastructure.common.errors import register_error_handlers
from infrastructure.oauth.session.api import router as token_router

api = NinjaAPI(version="1.0.0", urls_namespace="oauth-session-tests")
api.add_router("/auth/password", password_router)
api.add_router("/auth/token", token_router)
register_error_handlers(api)
register_auth_exception_handlers(api)

urlpatterns = [path("api/v1/", api.urls)]
