"""The WinGet manifests written for a release (packaging/winget/make_manifests.py)."""

from __future__ import annotations

import datetime
import hashlib
import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

_PACKAGING = Path(__file__).resolve().parent.parent / "packaging"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "make_manifests", _PACKAGING / "winget" / "make_manifests.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_manifests = _load()


@pytest.fixture
def files(tmp_path: Path) -> dict[str, str]:
    installers = {}
    for arch in ("x64", "arm64"):
        path = tmp_path / f"XKNX-Editor-windows-installer-{arch}.exe"
        path.write_bytes(arch.encode())
        installers[arch] = path
    return make_manifests.manifests(
        "xknx-editor-v1.2.3", installers, datetime.date(2026, 10, 17)
    )


def test_the_product_code_is_the_app_id_of_the_installer():
    iss = (_PACKAGING / "windows" / "xknx-editor.iss").read_text(encoding="utf-8-sig")
    app_id = re.search(r"^AppId=\{(\{.+\})$", iss, re.M)
    assert app_id is not None
    assert make_manifests.product_code() == f"{app_id.group(1)}_is1"


def test_one_version_installer_and_locale_manifest_each(files: dict[str, str]):
    assert sorted(files) == [
        "XKNX.Editor.installer.yaml",
        "XKNX.Editor.locale.de-DE.yaml",
        "XKNX.Editor.locale.en-US.yaml",
        "XKNX.Editor.yaml",
    ]
    for text in files.values():
        assert "PackageIdentifier: XKNX.Editor\nPackageVersion: 1.2.3\n" in text
    assert "ManifestType: defaultLocale" in files["XKNX.Editor.locale.en-US.yaml"]
    assert "ManifestType: locale" in files["XKNX.Editor.locale.de-DE.yaml"]


def test_each_architecture_installs_per_user_or_for_all_users(files: dict[str, str]):
    installer = files["XKNX.Editor.installer.yaml"]
    for arch in ("x64", "arm64"):
        url = (
            "https://github.com/XKNX/xknx-editor/releases/download/xknx-editor-v1.2.3/"
            f"XKNX-Editor-windows-installer-{arch}.exe"
        )
        digest = hashlib.sha256(arch.encode()).hexdigest().upper()
        for scope, switch in (("user", "/CURRENTUSER"), ("machine", "/ALLUSERS")):
            assert (
                f"- Architecture: {arch}\n  Scope: {scope}\n  InstallerUrl: {url}\n"
                f"  InstallerSha256: {digest}\n  InstallerSwitches:\n    Custom: {switch}\n"
            ) in installer
    assert installer.count("ElevationRequirement: elevatesSelf") == 2
    assert "ReleaseDate: 2026-10-17" in installer
    assert "FileExtensions:\n- xknx\n" in installer


def test_other_tags_are_refused(tmp_path: Path):
    with pytest.raises(ValueError):
        make_manifests.manifests(
            "xknxeditor-namespaces-v1.0.0", {}, datetime.date(2026, 1, 1)
        )
