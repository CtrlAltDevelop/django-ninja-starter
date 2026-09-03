"""Message shapes for the account service's nested profile.

Everywhere else in this project a message is declared as a list of field
dictionaries, which is enough for a flat message. A *nested* one has to be a
type protoc can name, and django-socio-grpc builds those from serializers -- so
the one nested message in this app is declared as one. It carries no behaviour:
the fields are the declaration, and :class:`AccountService` does the deciding.
"""

from rest_framework import serializers


class Profile(serializers.Serializer[dict[str, object]]):
    """The profile, as it appears inside an ``Account`` message.

    Named for the message rather than for the class: the generator uses the
    class name verbatim for a nested field's type, so calling this
    ``ProfileSerializer`` would put that word on the wire.
    """

    display_name = serializers.CharField()
    avatar_url = serializers.CharField()
    bio = serializers.CharField()
    locale = serializers.CharField()
    timezone = serializers.CharField()
    date_of_birth = serializers.CharField()
    marketing_opt_in = serializers.BooleanField()
