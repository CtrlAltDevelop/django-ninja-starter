"""The social-login messages, shared by every provider.

A browser follows a redirect and comes back with a cookie. A gRPC client cannot,
so the flow is turned inside out: ``Start`` hands back the URL to open and the
*binding* that proves the attempt was this client's, and ``Complete`` takes both
back along with whatever the provider put in the callback. The checks are
identical -- same state row, same binding digest, same account resolution --
because they are the same service.
"""

from typing import Any

from rest_framework import serializers

START_RESPONSE = [
    {"name": "authorization_url", "type": "string"},
    {"name": "binding", "type": "string"},
    {"name": "expires_in", "type": "int32"},
]


class CallbackParameter(serializers.Serializer[dict[str, object]]):
    """One name/value pair out of the provider's callback."""

    name = serializers.CharField()
    value = serializers.CharField()


COMPLETE_REQUEST = [
    {"name": "binding", "type": "string"},
    {"name": "callback", "cardinality": "repeated", "type": CallbackParameter},
]


def callback_data(request: Any) -> dict[str, str]:
    """Flatten the repeated pairs back into the dictionary a provider expects."""
    return {parameter.name: parameter.value for parameter in request.callback}
