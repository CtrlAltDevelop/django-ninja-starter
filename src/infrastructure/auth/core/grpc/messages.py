"""The login messages every authentication method answers with.

Declared once, in the two forms django-socio-grpc needs: serializers for the
messages that are nested inside another, and field lists for the ones that are
not. What they describe is the same contract the REST schemas and the GraphQL
types describe -- see :mod:`infrastructure.auth.core.rest.schemas`.

``LoginResult`` is imported only for type checking, for the same reason
:mod:`infrastructure.auth.core.graph.types` does: a project may install the
OAuth apps without the authentication core, and importing ``flows`` at module
scope would pull in models that project has no tables for.
"""

from typing import TYPE_CHECKING, Any

from rest_framework import serializers

if TYPE_CHECKING:
    from infrastructure.auth.core.flows import LoginResult

MESSAGE_RESPONSE = [{"name": "detail", "type": "string"}]
CHALLENGE_RESPONSE = [
    {"name": "ticket", "type": "string"},
    {"name": "channel", "type": "string"},
    {"name": "destination", "type": "string"},
    {"name": "expires_in", "type": "int32"},
]


class Credentials(serializers.Serializer[dict[str, object]]):
    """The credential a finished login hands back.

    Named for the message rather than for the class: a nested field takes the
    class name verbatim, so ``CredentialsSerializer`` would end up on the wire.
    """

    token_type = serializers.CharField()
    access_token = serializers.CharField()
    refresh_token = serializers.CharField()
    expires_in = serializers.IntegerField()
    session_id = serializers.CharField()


LOGIN_RESPONSE = [
    {"name": "requires_second_factor", "type": "bool"},
    {"name": "credentials", "type": Credentials},
    {"name": "login_ticket", "type": "string"},
    {"name": "methods", "cardinality": "repeated", "type": "string"},
]


def login_message(result: "LoginResult", pb2: Any) -> Any:
    """Render a login outcome into an app's own generated ``LoginResult`` message.

    Each app compiles its own copy of the message, so the module is passed in
    rather than imported: the shape is shared, the generated class is not.

    Named ``LoginResult`` rather than ``Login`` because proto3 puts messages and
    rpc methods in one namespace, and most of these apps publish a ``Login`` rpc.
    """
    if result.credentials is not None:
        return pb2.LoginResult(
            requires_second_factor=False,
            credentials=pb2.Credentials(
                token_type=result.credentials.token_type,
                access_token=result.credentials.access_token,
                refresh_token=result.credentials.refresh_token,
                expires_in=result.credentials.expires_in or 0,
                session_id=result.credentials.session_id,
            ),
        )
    return pb2.LoginResult(
        requires_second_factor=True,
        login_ticket=result.pending_ticket,
        methods=result.methods,
    )
