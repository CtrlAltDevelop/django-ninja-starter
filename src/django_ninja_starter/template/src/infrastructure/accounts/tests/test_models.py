"""The account model, its manager, and the profile that always accompanies it."""

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.utils import timezone

from infrastructure.accounts.models import Profile

User = get_user_model()


@pytest.fixture
def user(db: None) -> Any:
    return User._default_manager.create_user("zoe", email="zoe@example.com")


def test_a_new_account_gets_a_profile_without_being_asked(user: Any) -> None:
    """Every caller depends on this, so nothing has to defend against its absence."""
    assert user.profile is not None
    assert Profile.objects.count() == 1


def test_the_profile_starts_with_the_projects_defaults(settings: Any, db: None) -> None:
    settings.ACCOUNTS_DEFAULT_LOCALE = "pt-br"
    settings.ACCOUNTS_DEFAULT_TIMEZONE = "Europe/Lisbon"

    created = User._default_manager.create_user("novo")

    assert created.profile.locale == "pt-br"
    assert created.profile.timezone == "Europe/Lisbon"


def test_saving_an_account_again_does_not_make_a_second_profile(user: Any) -> None:
    user.email = "elsewhere@example.com"
    user.save()

    assert Profile.objects.count() == 1


def test_an_account_created_without_a_password_cannot_be_signed_into_with_one(
    user: Any,
) -> None:
    """Unusable rather than empty, so it cannot be mistaken for one never set."""
    assert user.has_usable_password() is False
    assert user.check_password("") is False


def test_an_account_created_with_a_password_keeps_it(db: None) -> None:
    created = User._default_manager.create_user("zoe", password="corr3ct-horse-battery")

    assert created.has_usable_password() is True
    assert created.check_password("corr3ct-horse-battery") is True


def test_an_address_is_stored_lowercased(db: None) -> None:
    created = User._default_manager.create_user("zoe", email="Zoe@Example.COM")

    assert created.email == "zoe@example.com"


def test_an_account_with_no_address_stores_null_rather_than_empty(db: None) -> None:
    """Two empty strings would collide on the unique column; two nulls do not."""
    first = User._default_manager.create_user("phone-only-a")
    second = User._default_manager.create_user("phone-only-b")

    assert first.email is None
    assert second.email is None


def test_two_accounts_cannot_share_an_address(user: Any) -> None:
    with pytest.raises(IntegrityError):
        User._default_manager.create_user("someone-else", email="zoe@example.com")


def test_two_accounts_cannot_share_a_username(user: Any) -> None:
    with pytest.raises(IntegrityError):
        User._default_manager.create_user("zoe")


def test_an_account_needs_a_username(db: None) -> None:
    with pytest.raises(ValueError, match="needs a username"):
        User._default_manager.create_user("")


def test_a_superuser_is_staff_and_super(db: None) -> None:
    root = User._default_manager.create_superuser("root", password="irrelevant")

    assert root.is_staff is True
    assert root.is_superuser is True


def test_a_superuser_that_is_not_super_is_refused(db: None) -> None:
    with pytest.raises(ValueError, match="must have is_staff and is_superuser"):
        User._default_manager.create_superuser("root", is_superuser=False)


def test_an_address_starts_unverified_and_can_be_confirmed_once(user: Any) -> None:
    assert user.is_email_verified is False

    user.mark_email_verified()
    first = user.email_verified_at

    user.mark_email_verified()

    assert user.is_email_verified is True
    assert user.email_verified_at == first, "confirming twice must not move the timestamp"


def test_an_account_describes_itself_by_its_username(user: Any) -> None:
    assert str(user) == "zoe"
    assert user.get_full_name() == "zoe"
    assert user.get_short_name() == "zoe"


def test_a_display_name_takes_over_once_there_is_one(user: Any) -> None:
    user.profile.display_name = "Zoe Adeyemi"
    user.profile.save()

    assert user.get_full_name() == "Zoe Adeyemi"
    assert user.get_short_name() == "Zoe"


def test_filling_blanks_sets_only_what_is_empty(user: Any) -> None:
    user.profile.display_name = "Chosen By Me"
    user.profile.save()

    changed = user.profile.fill_blanks(
        display_name="From The Provider",
        avatar_url="https://example.test/a.png",
    )

    user.profile.refresh_from_db()
    assert changed == ["avatar_url"]
    assert user.profile.display_name == "Chosen By Me"
    assert user.profile.avatar_url == "https://example.test/a.png"


def test_filling_blanks_with_nothing_writes_nothing(user: Any) -> None:
    before = user.profile.updated_at

    assert user.profile.fill_blanks(display_name="", avatar_url="") == []

    user.profile.refresh_from_db()
    assert user.profile.updated_at == before


def test_deleting_an_account_takes_its_profile_with_it(user: Any) -> None:
    user.delete()

    assert Profile.objects.count() == 0


def test_the_profile_describes_itself_by_display_name_then_id(user: Any) -> None:
    assert str(user.profile) == str(user.pk)

    user.profile.display_name = "Zoe"

    assert str(user.profile) == "Zoe"


def test_an_account_is_ordered_newest_first(db: None) -> None:
    older = User._default_manager.create_user("older")
    User.objects.filter(pk=older.pk).update(date_joined=timezone.now() - timedelta(days=1))
    newer = User._default_manager.create_user("newer")

    assert list(User.objects.all()) == [newer, older]
