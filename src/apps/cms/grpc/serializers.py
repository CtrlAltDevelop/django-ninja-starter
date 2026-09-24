"""The nested messages the content service answers with.

A field's ``value`` is the awkward one: its shape is decided by its ``type``,
which the same message carries, so there is no single protobuf type that fits.
It travels as ``value_json`` -- the JSON encoding of whatever the field holds --
and a client parses it according to the ``type`` beside it. The same is true of
the site's free-form ``contact``, ``social_links`` and ``extra``.
"""

from rest_framework import serializers


class ContentField(serializers.Serializer[dict[str, object]]):
    """One piece of content, already resolved to the requested language."""

    id = serializers.CharField()
    name = serializers.CharField()
    type = serializers.CharField()
    multiple = serializers.BooleanField()
    required = serializers.BooleanField()
    value_json = serializers.CharField()


class Section(serializers.Serializer[dict[str, object]]):
    """A section and its fields.

    Deliberately one level deep: protobuf has no recursive message shorthand,
    and nesting a `Section` inside itself would have to be declared by hand. The
    children carry their own fields, which is what a renderer needs.
    """

    id = serializers.CharField()
    name = serializers.CharField()
    shared = serializers.BooleanField()
    fields = ContentField(many=True)
    #: The id of the section this one sits inside, empty at the top level, so the
    #: flat list can be put back into the tree REST and GraphQL return.
    parent = serializers.CharField()


class PageMeta(serializers.Serializer[dict[str, object]]):
    title = serializers.CharField()
    description = serializers.CharField()
    og_title = serializers.CharField()
    og_description = serializers.CharField()
    og_image = serializers.CharField()
    og_url = serializers.CharField()


class MenuChild(serializers.Serializer[dict[str, object]]):
    """An entry under another. One level, as the menu itself nests only one."""

    label = serializers.CharField()
    page = serializers.CharField()
    url = serializers.CharField()
    new_tab = serializers.BooleanField()


class MenuItem(serializers.Serializer[dict[str, object]]):
    """One entry. ``page`` names a page in this CMS; ``url`` is anything else."""

    label = serializers.CharField()
    page = serializers.CharField()
    url = serializers.CharField()
    new_tab = serializers.BooleanField()
    children = MenuChild(many=True)


class PageSummary(serializers.Serializer[dict[str, object]]):
    """A row in a page list: ``id`` is what the page call is asked for."""

    id = serializers.CharField()
    name = serializers.CharField()
    title = serializers.CharField()
    order = serializers.IntegerField()
    updated_at = serializers.CharField()


class MenuSummary(serializers.Serializer[dict[str, object]]):
    id = serializers.CharField()
    name = serializers.CharField()
