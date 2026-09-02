"""Password checks that behave the same whether or not the account exists."""

from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from infrastructure.auth.core.errors import AuthError
from infrastructure.common.responses import ResponseTitle

LOGIN_ATTEMPT_LIMIT = 10
RESET_PURPOSE = "password_reset"


def enforce_password_policy(password: str, user: Any | None = None) -> None:
    """Apply the project's ``AUTH_PASSWORD_VALIDATORS`` to a proposed password."""
    try:
        validate_password(password, user)
    except ValidationError as error:
        raise AuthError(
            " ".join(error.messages), status=400, title=ResponseTitle.WEAK_PASSWORD
        ) from error


def burn_timing() -> None:
    """Hash a throwaway password so a missing account costs the same as a wrong one.

    Without this, the response time alone tells an attacker which identifiers are
    real, which is most of the work of enumerating a user base.
    """
    get_user_model()().set_password("no-such-account")


def password_matches(user: Any | None, password: str) -> bool:
    if user is None:
        burn_timing()
        return False
    return bool(user.check_password(password))
