"""The boot-time refusal, which is what makes a settings contract binding.

``manage.py`` has always run the system checks, which is why a missing setting
stops ``runserver`` and ``migrate``. Nothing ran them when Gunicorn or Uvicorn
imported ``config.wsgi`` or ``config.asgi``, so in the one environment where a
wrong setting matters most the process started cleanly and failed later, one
request at a time.

These tests are about the entry points rather than about the checks: that a
configuration error stops the process, that a warning does not, and that the way
out is deliberate.
"""

import pytest
from django.core.checks import Error, Warning
from django.core.management.base import SystemCheckError

from config.preflight import verify_configuration


def _returns(monkeypatch: pytest.MonkeyPatch, messages: list[Error | Warning]) -> None:
    monkeypatch.setattr("config.preflight.run_checks", lambda **kwargs: messages)


def test_a_clean_configuration_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    _returns(monkeypatch, [])

    verify_configuration()


def test_an_error_refuses_to_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    _returns(monkeypatch, [Error("Shop needs SHOP_CURRENCY", id="shop.SHOP_CURRENCY")])

    with pytest.raises(SystemCheckError) as raised:
        verify_configuration()

    assert "Shop needs SHOP_CURRENCY" in str(raised.value)
    assert "1 configuration error" in str(raised.value)


def test_the_report_names_every_error_not_only_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment that fixes one setting, reboots, and is then told about the
    next one pays a restart per mistake."""
    _returns(
        monkeypatch,
        [
            Error("Shop needs SHOP_CURRENCY", id="shop.SHOP_CURRENCY"),
            Error("Content needs CMS_UPLOAD_PATH", id="cms.CMS_UPLOAD_PATH"),
        ],
    )

    with pytest.raises(SystemCheckError) as raised:
        verify_configuration()

    assert "SHOP_CURRENCY" in str(raised.value)
    assert "CMS_UPLOAD_PATH" in str(raised.value)


def test_a_warning_does_not_stop_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refusing to boot over an opinion would make the warning level useless."""
    _returns(monkeypatch, [Warning("in-memory broker", id="support.SUPPORT_BROKER")])

    verify_configuration()


def test_a_silenced_error_does_not_stop_the_process(
    monkeypatch: pytest.MonkeyPatch, settings: pytest.FixtureRequest
) -> None:
    """`SILENCED_SYSTEM_CHECKS` is how a deployment overrules one message, and it
    has to mean the same thing here as it does to `manage.py check`."""
    settings.SILENCED_SYSTEM_CHECKS = ["shop.SHOP_CURRENCY"]
    _returns(monkeypatch, [Error("Shop needs SHOP_CURRENCY", id="shop.SHOP_CURRENCY")])

    verify_configuration()


def test_the_escape_hatch_has_to_be_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    _returns(monkeypatch, [Error("Shop needs SHOP_CURRENCY", id="shop.SHOP_CURRENCY")])
    monkeypatch.setenv("DJANGO_SKIP_PREFLIGHT", "true")

    verify_configuration()


def test_the_escape_hatch_is_off_unless_it_says_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """So that `DJANGO_SKIP_PREFLIGHT=1`, or a stray value, does not quietly
    disarm the one thing between a bad deploy and a served request."""
    _returns(monkeypatch, [Error("Shop needs SHOP_CURRENCY", id="shop.SHOP_CURRENCY")])
    monkeypatch.setenv("DJANGO_SKIP_PREFLIGHT", "1")

    with pytest.raises(SystemCheckError):
        verify_configuration()
