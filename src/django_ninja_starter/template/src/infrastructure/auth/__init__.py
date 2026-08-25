"""First-party authentication: login methods and second factors.

Each subpackage is an independently installable Django app, enabled through
``DJANGO_AUTH_METHODS`` and ``DJANGO_AUTH_SECOND_FACTORS``. They keep their
original app labels, so the module layout is free to change without touching a
table name or a migration.
"""
