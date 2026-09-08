"""apple sign-in's contribution to the project's GraphQL schema.

Nothing provider-specific: the fields come from the shared builders, bound to
this provider. A deployment that enables more than one provider gets the field
names from whichever apps it installed -- see ``config.graph`` for why a
collision is a startup error rather than a silently shadowed field.
"""

from infrastructure.oauth.apple.provider import provider
from infrastructure.oauth.core.graph.social import social_mutation, social_query

Query = social_query(provider)
Mutation = social_mutation(provider)
