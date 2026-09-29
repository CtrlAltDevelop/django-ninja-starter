"""Refuse to serve a deployment whose installed apps are misconfigured.

``manage.py`` runs the system checks before it runs a command, which is why a
missing setting stops ``runserver`` and ``migrate``. Nothing runs them when
Gunicorn or Uvicorn imports ``config.wsgi`` or ``config.asgi`` -- so in the one
environment where a wrong setting matters most, the process used to start
cleanly and fail later, one request at a time, in whichever worker happened to
take it.

An app's settings contract is only worth declaring if something enforces it
everywhere the app runs. So both entry points call this first, and a deployment
that turned an app on without finishing its configuration finds out at boot,
with the same message ``manage.py check`` would have given it.

``DJANGO_SKIP_PREFLIGHT=true`` opts out, for the rare deployment that would
rather serve degraded than not at all. It is deliberately not the default: a
starter that ships a lenient production entry point teaches the wrong lesson.

**The checks touch the database, so they cannot run on an event loop.** Some of
Django's own model checks open a cursor -- asking SQLite whether it supports
``JSONField`` is one -- and doing that from asynchronous code raises
``SynchronousOnlyOperation``. An ASGI server that imports the application from
inside its loop, which is what ``uvicorn --reload`` does, would therefore die on
boot with a traceback about asynchronous context and nothing about settings. So
the checks are run on a worker thread whenever there is a loop running, which is
the remedy Django's own message points at.
"""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from django.core.checks import CheckMessage, run_checks
from django.core.management.base import SystemCheckError


def verify_configuration() -> None:
    """Raise :class:`SystemCheckError` if any installed app is missing a setting.

    Errors only. A warning says the deployment is doing something inadvisable --
    an in-memory broker under several workers, say -- and refusing to boot over
    an opinion would make the warning level useless.
    """
    if os.getenv("DJANGO_SKIP_PREFLIGHT", "false").lower() == "true":
        return

    _run_checks()


def _run_checks() -> None:
    """Run the checks on this thread, or on a worker if this one is a loop's.

    ``get_running_loop`` rather than a flag passed in by each entry point: what
    matters is the thread the checks would actually run on, and only the runtime
    knows that -- ``config.wsgi`` under Gunicorn and ``config.asgi`` under
    uvicorn reach this same line.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _report(_serious())
        return
    with ThreadPoolExecutor(max_workers=1) as pool:
        _report(pool.submit(_serious).result())


def _serious() -> list[CheckMessage]:
    """Every error the installed apps reported, silenced ones left out."""
    return [
        message
        for message in run_checks(include_deployment_checks=False)
        if message.is_serious() and not message.is_silenced()
    ]


def _report(serious: list[CheckMessage]) -> None:
    """Refuse to serve, naming every error, or return and let the process start."""
    if not serious:
        return

    report = "\n".join(f"{message}" for message in serious)
    raise SystemCheckError(
        f"Refusing to serve: {len(serious)} configuration error(s).\n\n{report}\n\n"
        "Run `manage.py check` for the same report, fix the settings the apps you "
        "enabled require, or set DJANGO_SKIP_PREFLIGHT=true to serve anyway."
    )
