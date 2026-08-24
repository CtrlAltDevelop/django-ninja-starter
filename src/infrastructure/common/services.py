"""Services shared by infrastructure endpoints."""

from django.db import connections
from django.db.utils import OperationalError


def database_is_ready(alias: str = "default") -> bool:
    """Return whether the configured database accepts a trivial query."""
    try:
        with connections[alias].cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except OperationalError:
        return False
    return True
