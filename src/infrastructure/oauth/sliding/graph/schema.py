"""The sliding mode's contribution to the project's GraphQL schema.

Nothing mode-specific: the fields come from the shared builders, bound to this
mode's service.

Contributed only when this mode is the *active* one. A deployment may install
all three apps -- ``DJANGO_OAUTH_MODE=all`` does -- but only one of them answers
at ``/auth/token``, and the same has to be true of the field named ``sessions``.
"""

from django.conf import settings

from infrastructure.oauth.core.graph.tokens import token_mutation, token_query
from infrastructure.oauth.sliding.services import token_service

_active = settings.AUTH_TOKEN_MODE == "sliding"
Query = token_query(token_service) if _active else None
Mutation = token_mutation(token_service) if _active else None
