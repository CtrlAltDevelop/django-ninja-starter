"""The HTTP transport for magic-link authentication.

Everything this app publishes is a way in or out, so `login.py` is the whole of
it; `router` is what the project mounts.
"""

from infrastructure.auth.magic_link.rest.login import router

__all__ = ["router"]
