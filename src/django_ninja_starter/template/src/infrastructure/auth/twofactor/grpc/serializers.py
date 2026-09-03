"""The one nested message this app publishes.

A flat message is declared as a list of field dictionaries; a nested one has to
be a type protoc can name, and django-socio-grpc builds those from serializers.
Named for the message rather than for the class, because a nested field takes
the class name verbatim.
"""

from rest_framework import serializers


class EnrolledFactor(serializers.Serializer[dict[str, object]]):
    """One second factor on an account, as it appears inside a ``FactorList``."""

    method = serializers.CharField()
    destination = serializers.CharField()
    confirmed = serializers.BooleanField()
    last_used_at = serializers.CharField()
