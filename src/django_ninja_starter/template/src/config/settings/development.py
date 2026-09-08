import os

from config.settings.base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# The in-browser GraphQL editor, on unless this environment says otherwise.
# Re-read here rather than derived from `DEBUG` in `base`, because `base` reads
# its own `DEBUG` -- which is False -- long before the line above raises it.
GRAPHQL_GRAPHIQL = os.getenv("DJANGO_GRAPHQL_GRAPHIQL", "true").lower() == "true"
