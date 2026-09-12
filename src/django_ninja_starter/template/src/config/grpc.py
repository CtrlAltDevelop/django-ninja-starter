"""Register every installed app's gRPC services with the running server.

django-socio-grpc calls ``grpc_handlers`` once, with the server it is about to
start, and expects every service to be registered against it. The discovery
here is the same convention the GraphQL schema uses: an app that publishes gRPC
puts a ``grpc/services.py`` beside its ``rest/`` package and names the service
classes in ``GRPC_SERVICES``. An app that is not installed registers nothing.

``manage.py generateproto`` calls this too, with no server, which is how the
``.proto`` files stay in step with the services rather than being hand-edited.
"""

import logging
from importlib import import_module, util
from types import ModuleType
from typing import Any

from django.apps import AppConfig, apps
from django.conf import settings
from django_socio_grpc.protobuf import RegistrySingleton
from django_socio_grpc.request_transformer.grpc_internal_container import GRPCRequestContainer
from django_socio_grpc.services.app_handler_registry import AppHandlerRegistry

logger = logging.getLogger(__name__)


def _patch_request_container_for_python_314() -> None:
    """Make django-socio-grpc's request container settable on Python 3.14.

    Its ``__setattr__`` asks ``self.__annotations__`` whether a name is one of
    the dataclass's own fields. Up to 3.13 that read fell through to the class;
    since 3.14 it does not, so it lands in ``__getattr__``, which asks for
    ``self.context`` -- which is itself unset, and asks again. Every RPC dies in
    a recursion error before it reaches a service.

    The fix is to ask the class, which is what the code meant. Delete this once
    django-socio-grpc ships a release that runs on 3.14.
    """

    def __setattr__(self: Any, attr: str, value: Any) -> None:
        if attr in type(self).__annotations__:
            object.__setattr__(self, attr, value)
        else:
            setattr(self.context.grpc_context, attr, value)

    GRPCRequestContainer.__setattr__ = __setattr__


_patch_request_container_for_python_314()


def _app_grpc_module(app_name: str) -> ModuleType | None:
    """Import one app's ``grpc.services``, or return ``None`` if it has none.

    As in ``config.graph``: a missing package means an app that does not publish
    gRPC, while an import error from inside the package is that app's bug and is
    re-raised rather than silently dropping its services from the server.
    """
    module_name = f"{app_name}.grpc.services"
    try:
        return import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name in {module_name, f"{app_name}.grpc"}:
            return None
        raise


def grpc_app_services(*, serving: bool = True) -> list[tuple[AppConfig, list[type]]]:
    """Return every installed app that publishes gRPC, with its services.

    An app may declare ``GRPC_SERVED = False`` to mean "generate my proto, but do
    not answer": the three token modes can all be installed while only one of
    them is the active one. Generation ignores that flag, so the committed stubs
    stay in step whichever mode a developer happens to be running.
    """
    found: list[tuple[AppConfig, list[type]]] = []
    for config in apps.get_app_configs():
        module = _app_grpc_module(config.name)
        if module is None:
            continue
        if serving and not getattr(module, "GRPC_SERVED", True):
            continue
        services = list(getattr(module, "GRPC_SERVICES", ()))
        if services:
            found.append((config, services))
    return found


def _stubs_compiled(config: AppConfig) -> bool:
    """Whether ``manage.py protos`` has been run for this app yet.

    A freshly scaffolded app has services and no ``.proto``. Registering it would
    fail while importing a module protoc has not written, taking the whole server
    down over an app that is one command away from working -- so it is skipped,
    loudly, until that command has been run.
    """
    module = f"{config.module.__name__}.grpc.{config.label}_pb2_grpc"
    return util.find_spec(module) is not None


def grpc_handlers(server: Any) -> None:
    """Attach every installed app's services to ``server``.

    The registry django-socio-grpc keeps is process-global and refuses to hand
    the same app to a second server, so this clears it first. A hook that can
    only ever run once would mean a test suite could stand up exactly one server
    per process, and `generateproto` could not run in the same process either.
    """
    RegistrySingleton.clean_all()
    serving = server is not None
    # `DJANGO_GRPC_ENABLED=false` means this deployment does not speak gRPC, so
    # nothing is registered against the server. It is deliberately not honoured
    # on the generation pass: `manage.py protos` has to be able to keep the
    # `.proto` files in step in a project that never serves them.
    if serving and not settings.GRPC_ENABLED:
        return
    # `generateproto` calls this with no server: that is the generation pass, and
    # it wants every app's document, not only the ones this deployment serves.
    for config, services in grpc_app_services(serving=serving):
        if serving and not _stubs_compiled(config):
            logger.warning(
                "Skipping gRPC services for %s: no compiled stubs. Run `manage.py protos`.",
                config.label,
            )
            continue
        registry = AppHandlerRegistry(config.label, server)
        for service in services:
            registry.register(service)
