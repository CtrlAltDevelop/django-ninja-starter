"""Regenerate every app's ``.proto`` file and its Python stubs.

django-socio-grpc ships ``generateproto``, and it very nearly does this already:
it writes each app's ``.proto`` from the actions the services declare. What it
cannot do here is compile them, because it runs ``protoc`` with the repository
root as the include path -- and this project's import root is ``src/``, one
level down. The stubs it produced would import each other as
``src.infrastructure.…``, which is a path that only resolves by accident of the
repository layout and not at all in an installed project.

So: let it write the documents, and compile them from the right root.

The stubs come with ``.pyi`` type stubs alongside them (mypy-protobuf), so a
message field that a service misspells is a type error rather than an
``AttributeError`` raised while answering a call.

    python manage.py protos          # rewrite .proto files and stubs
    python manage.py protos --check  # fail if either is out of date
"""

import filecmp
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from grpc_tools import protoc

# Only present in this repository, never in a generated project: the template is
# a *copy* of the tree, and compiling its protos would name every message after
# the path they sit at inside the generator.
TEMPLATE_MARKER = "django_ninja_starter/template/"


def _generated_template(proto: Path) -> bool:
    return TEMPLATE_MARKER in proto.as_posix()


def _plugin(name: str) -> str | None:
    """Find a protoc plugin, looking beside this interpreter before the PATH.

    ``protoc`` resolves plugins through the PATH, which does not include a
    virtual environment's ``bin`` unless it happens to be activated. Passing the
    absolute path makes generation work the same from a Makefile, an editor, or
    CI.
    """
    beside_python = Path(sys.executable).parent / name
    if beside_python.exists():
        return str(beside_python)
    return shutil.which(name)


def _typed_stub_arguments(source_root: Path) -> list[str]:
    """Ask for ``.pyi`` stubs as well, when mypy-protobuf is installed.

    They are what makes a misspelled message field a type error rather than an
    ``AttributeError`` raised while answering a call. A project that has not
    installed the development extra still gets working stubs, just untyped ones.
    """
    plugin = _plugin("protoc-gen-mypy")
    grpc_plugin = _plugin("protoc-gen-mypy_grpc")
    if not plugin or not grpc_plugin:
        return []
    return [
        f"--plugin=protoc-gen-mypy={plugin}",
        f"--plugin=protoc-gen-mypy_grpc={grpc_plugin}",
        f"--mypy_out=quiet:{source_root}",
        f"--mypy_grpc_out=quiet:{source_root}",
    ]


class Command(BaseCommand):
    help = "Regenerate the .proto files and Python stubs for every gRPC service."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Fail if a .proto file or stub is not what the services would produce.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        source_root = Path(settings.BASE_DIR) / "src"
        if options["check"]:
            call_command("generateproto", check=True, no_generate_pb2=True)
        else:
            call_command("generateproto", no_generate_pb2=True)
        self._compile(source_root, check=options["check"])

    def _compile(self, source_root: Path, *, check: bool) -> None:
        """Run ``protoc`` over every ``.proto`` under the import root.

        Under ``--check`` the stubs are written to a scratch directory and
        compared, so a check never edits the tree it is checking.
        """
        protos = sorted(
            proto for proto in source_root.glob("**/grpc/*.proto") if not _generated_template(proto)
        )
        if not protos:
            raise CommandError("No .proto files were generated; is any gRPC service registered?")

        if not check:
            self._run(source_root, source_root, protos)
            for proto in protos:
                self.stdout.write(f"  {proto.relative_to(source_root)}")
            return

        with tempfile.TemporaryDirectory() as scratch:
            self._run(source_root, Path(scratch), protos)
            for proto in protos:
                for stub in self._stubs(proto):
                    fresh = Path(scratch) / stub.relative_to(source_root)
                    if not stub.exists() or not filecmp.cmp(stub, fresh, shallow=False):
                        raise CommandError(
                            f"{stub.relative_to(source_root)} is out of date. "
                            "Run `manage.py protos`."
                        )
                self.stdout.write(f"  {proto.relative_to(source_root)}")

    def _stubs(self, proto: Path) -> list[Path]:
        """Every file ``protoc`` writes for one ``.proto``."""
        suffixes = ["_pb2.py", "_pb2_grpc.py"]
        if _plugin("protoc-gen-mypy"):
            suffixes += ["_pb2.pyi", "_pb2_grpc.pyi"]
        return [proto.with_name(f"{proto.stem}{suffix}") for suffix in suffixes]

    def _run(self, source_root: Path, out_root: Path, protos: list[Path]) -> None:
        from grpc_tools import _protoc_compiler

        include = Path(_protoc_compiler.__file__).parent / "_proto"
        for proto in protos:
            code = protoc.main(
                [
                    "",
                    f"--proto_path={source_root}",
                    f"--python_out={out_root}",
                    f"--grpc_python_out={out_root}",
                    *_typed_stub_arguments(out_root),
                    str(proto),
                    f"-I{include}",
                ]
            )
            if code != 0:
                raise CommandError(f"protoc failed on {proto.relative_to(source_root)}")
