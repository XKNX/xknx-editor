"""The release files for the Home Assistant integration (packaging/home_assistant/release_files.py)."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_PACKAGING = Path(__file__).resolve().parent.parent / "packaging"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_files", _PACKAGING / "home_assistant" / "release_files.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release_files = _load()

_EXPORTED = """\
aiofile==3.12.3 \\
    --hash=sha256:5c1b \\
    --hash=sha256:caa6
    # via py-key-value-aio
xknxproject @ git+https://github.com/XKNX/xknxproject@f97db068884eca2b460abb1fb4db2262345f7f47
    # via xknxeditor-proj
zipp==3.23.0 \\
    --hash=sha256:0718
"""


def test_the_version_of_a_release_tag() -> None:
    assert release_files.version_of("xknx-editor-v0.9.0") == "0.9.0"
    with pytest.raises(SystemExit, match="not an XKNX Editor release tag"):
        release_files.version_of("xknxeditor-proj-v0.9.0")


def test_packages_from_git() -> None:
    assert release_files.git_sources(_EXPORTED) == [
        (
            "xknxproject",
            "https://github.com/XKNX/xknxproject",
            "f97db068884eca2b460abb1fb4db2262345f7f47",
        )
    ]


def test_the_requirements_file(tmp_path: Path) -> None:
    wheels = []
    for name in (
        "xknxproject-3.10.0-py3-none-any.whl",
        "xknxeditor_gui-0.9.0-py3-none-any.whl",
    ):
        wheel = tmp_path / name
        wheel.write_bytes(name.encode())
        wheels.append(wheel)
    base = "https://github.com/XKNX/xknx-editor/releases/download/xknx-editor-v0.9.0"

    text = release_files.requirements_text(_EXPORTED, wheels, base, "ab" * 32)
    lines = text.splitlines()

    # The integration finds the helpers' hash in the first line.
    assert lines[0] == f"# keyextract.zip sha256:{'ab' * 32}"
    # The packages from PyPI stay as exported, with their hashes.
    assert "aiofile==3.12.3 \\" in lines
    assert "zipp==3.23.0 \\" in lines
    # The git revision is replaced by its wheel, together with its indented lines.
    assert not any("git+" in line for line in lines)
    assert "    # via xknxeditor-proj" not in lines
    gui = hashlib.sha256(b"xknxeditor_gui-0.9.0-py3-none-any.whl").hexdigest()
    assert (
        f"xknxeditor-gui @ {base}/xknxeditor_gui-0.9.0-py3-none-any.whl \\\n"
        f"    --hash=sha256:{gui}"
    ) in text
    assert f"xknxproject @ {base}/xknxproject-3.10.0-py3-none-any.whl \\" in lines
    assert text.endswith("\n")


def test_the_helpers_are_built_like_in_the_docker_image() -> None:
    dockerfile = (_PACKAGING.parents[2] / "docker" / "Dockerfile").read_text(
        encoding="utf-8"
    )
    for name, source, package in release_files.HELPERS:
        assert f"{name}/{source}" in dockerfile
        if package:
            assert package in dockerfile
