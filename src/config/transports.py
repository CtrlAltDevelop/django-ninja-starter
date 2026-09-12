"""Which transports this deployment actually serves for one app.

``config/graph.py`` and ``config/grpc.py`` both discover their contributions by
walking the installed apps, and both have to skip an app that was installed to
serve REST only. That question is asked in two places and has to be answered the
same way in both, so it is answered here.

The mapping itself is built in the settings module, where the environment is
read; this only reads it, and treats an app that is not in it as publishing
everything. That is the right default rather than a lenient one: the apps
missing from it are the infrastructure ones, whose transports are the project's
own and are turned off by ``GRAPHQL_ENABLED`` and ``GRPC_ENABLED`` instead.
"""

from django.conf import settings


def serves(app_name: str, transport: str) -> bool:
    """Whether ``app_name`` publishes ``transport`` in this deployment.

    ``app_name`` is the dotted module name ``AppConfig.name`` carries -- the key
    the settings mapping uses -- rather than the short label, because two apps
    may share a label prefix and none share a module path.
    """
    transports = settings.APP_TRANSPORTS.get(app_name)
    return True if transports is None else transport in transports
