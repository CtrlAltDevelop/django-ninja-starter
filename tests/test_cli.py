import subprocess
import sys
from pathlib import Path

import pytest

from django_ninja_starter.cli import create_project, main


def test_create_project_renders_complete_starter(tmp_path: Path) -> None:
    target = create_project("billing-api", tmp_path / "generated")

    assert target == (tmp_path / "generated").resolve()
    assert (target / ".env.example").is_file()
    assert (target / ".github/workflows/ci.yml").is_file()
    assert (target / "src/apps/__init__.py").is_file()
    assert (target / "src/infrastructure/common/api.py").is_file()
    assert 'name = "billing-api"' in (target / "pyproject.toml").read_text()
    assert 'title="Billing Api API"' in (target / "src/config/api.py").read_text()

    generated_text = "\n".join(
        path.read_text()
        for path in target.rglob("*")
        if path.is_file() and path.suffix not in {".pyc"}
    )
    assert "{{ project_" not in generated_text


def test_generated_project_passes_django_check(tmp_path: Path) -> None:
    target = create_project("smoke-test", tmp_path / "smoke-test")

    result = subprocess.run(
        [sys.executable, "manage.py", "check"],
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "System check identified no issues" in result.stdout


@pytest.mark.parametrize("name", ["", "123-api", "spaces are invalid", "bad/name"])
def test_create_project_rejects_invalid_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="project name"):
        create_project(name, tmp_path / "output")


def test_create_project_refuses_nonempty_destination(tmp_path: Path) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("user content")

    with pytest.raises(FileExistsError, match="destination is not empty"):
        create_project("valid-name", target)


def test_main_creates_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "custom-output"
    monkeypatch.setattr(
        sys,
        "argv",
        ["django-ninja-starter", "sample_api", "--directory", str(target)],
    )

    main()

    assert (target / "manage.py").is_file()
