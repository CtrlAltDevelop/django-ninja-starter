import pytest
from django.contrib.auth import get_user_model

from infrastructure.auth.core.identities import (
    IdentityError,
    account_phone,
    create_user_for_email,
    create_user_for_phone,
    normalize_email,
    normalize_phone,
    user_by_email,
    user_by_login,
    user_by_phone,
)
from infrastructure.auth.core.models import PhoneNumber


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Alice@Example.COM ", "alice@example.com"),
        ("bob@example.com", "bob@example.com"),
    ],
)
def test_emails_are_lowercased_and_trimmed(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


def test_malformed_emails_are_refused() -> None:
    with pytest.raises(IdentityError, match="valid email"):
        normalize_email("not-an-address")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+1 (415) 555-0101", "+14155550101"),
        ("+44 20 7946 0958", "+442079460958"),
        (" +14155550101 ", "+14155550101"),
    ],
)
def test_phone_numbers_tolerate_human_spacing(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["4155550101", "+0155550101", "+123", "+1415555010112345"])
def test_numbers_outside_e164_are_refused(raw: str) -> None:
    with pytest.raises(IdentityError, match="international format"):
        normalize_phone(raw)


def test_an_email_shared_by_two_accounts_is_ambiguous(db: None) -> None:
    """Stock Django lets two users share an address; picking one would be a guess."""
    user_model = get_user_model()
    user_model._default_manager.create_user(username="a", email="shared@example.com")
    user_model._default_manager.create_user(username="b", email="shared@example.com")

    with pytest.raises(IdentityError, match="More than one account"):
        user_by_email("shared@example.com")


def test_email_lookup_ignores_case(db: None) -> None:
    user = get_user_model()._default_manager.create_user(username="a", email="Zoe@Example.com")

    assert user_by_email("zoe@example.com") == user


def test_login_resolves_a_username_or_an_email(db: None) -> None:
    user = get_user_model()._default_manager.create_user(username="zoe", email="zoe@example.com")

    assert user_by_login("zoe") == user
    assert user_by_login("zoe@example.com") == user
    assert user_by_login("nobody") is None


def test_a_created_email_account_has_no_usable_password(db: None) -> None:
    user = create_user_for_email("new@example.com")

    assert user.email == "new@example.com"
    assert user.has_usable_password() is False


def test_creating_two_accounts_from_one_local_part_does_not_collide(db: None) -> None:
    first = create_user_for_email("same@example.com")
    second = create_user_for_email("same@other.test")

    assert first.username != second.username


def test_a_created_phone_account_owns_a_verified_number(db: None) -> None:
    user = create_user_for_phone("+14155550101")

    record = PhoneNumber.objects.get(user=user)
    assert record.is_verified is True
    assert record.is_primary is True
    assert account_phone(user) == "+14155550101"


def test_unverified_numbers_do_not_answer_a_login_lookup(db: None) -> None:
    user = get_user_model()._default_manager.create_user(username="zoe")
    PhoneNumber.objects.create(user=user, number="+14155550102", is_verified=False)

    assert user_by_phone("+14155550102") is None
    assert user_by_phone("+14155550102", verified_only=False) == user
