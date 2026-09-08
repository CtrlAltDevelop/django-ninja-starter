"""The social-login fields, built once and bound to one provider.

A browser follows a redirect and comes back with a cookie. A GraphQL client
cannot, so the flow is turned inside out: `start` hands back the URL to open and
the *binding* that proves the attempt was this client's, and `complete` takes
both back along with whatever the provider put in the callback. The checks are
identical -- same state row, same binding digest, same account resolution --
because they are the same service.

What a completed login yields here is a credential rather than a session cookie,
which is what a non-browser client can actually use.

The fields are named for the provider -- ``googleStart``, ``appleComplete`` --
because a deployment may enable several, and GraphQL has one namespace for all
of them.
"""

from typing import Any

import strawberry
from strawberry.types import Info

from infrastructure.auth.core.graph.types import CredentialsType
from infrastructure.common.errors import ApiError
from infrastructure.common.graph.errors import resolver
from infrastructure.common.responses import ResponseTitle
from infrastructure.oauth.core.social import (
    OAuthProviderError,
    SocialProvider,
    social_login_service,
)


@strawberry.type
class SocialStartType:
    """Where to send the person, and what proves the attempt was this client's."""

    authorization_url: str
    binding: str
    expires_in: int


@strawberry.input
class CallbackParameter:
    """One name/value pair out of the provider's callback."""

    name: str
    value: str


def _refused(error: OAuthProviderError) -> ApiError:
    return ApiError(str(error), status=400, title=ResponseTitle.OAUTH_FAILED)


def social_query(provider: SocialProvider) -> type:
    """Build the read half: the URL that starts this provider's flow."""

    @strawberry.field(name=f"{provider.key}Start", description=f"Begin a {provider.key} sign-in.")
    @resolver
    def start(self: Any, info: Info[Any, Any]) -> SocialStartType:
        try:
            started = social_login_service.begin(info.context.request, provider)
        except OAuthProviderError as error:
            raise ApiError(
                str(error), status=503, title=ResponseTitle.OAUTH_NOT_CONFIGURED
            ) from error
        return SocialStartType(
            authorization_url=started.authorization_url,
            binding=started.binding,
            expires_in=started.expires_in,
        )

    # Built rather than declared, because the *attribute* name has to differ per
    # provider as well as the field name: strawberry merges contributions by
    # inheritance, and four classes all calling their resolver `start` would
    # leave one field where four were meant.
    return strawberry.type(type("Query", (), {f"{provider.key}_start": start}))


def social_mutation(provider: SocialProvider) -> type:
    """Build the write half: settling the callback into a credential."""

    @strawberry.mutation(
        name=f"{provider.key}Complete", description=f"Finish a {provider.key} sign-in."
    )
    @resolver
    def complete(
        self: Any,
        info: Info[Any, Any],
        binding: str,
        callback: list[CallbackParameter],
    ) -> CredentialsType:
        from infrastructure.auth.core.flows import record_event
        from infrastructure.auth.core.models import AuthEventType
        from infrastructure.auth.core.sessions import issue_credentials

        request = info.context.request
        data = {parameter.name: parameter.value for parameter in callback}
        try:
            completed = social_login_service.complete(provider, data, binding=binding)
        except OAuthProviderError as error:
            raise _refused(error) from error
        credentials = issue_credentials(request, completed.user, method=completed.method)
        record_event(
            request,
            AuthEventType.LOGIN_SUCCEEDED,
            user=completed.user,
            method=completed.method,
        )
        return CredentialsType.from_issued(credentials)

    return strawberry.type(type("Mutation", (), {f"{provider.key}_complete": complete}))
