"""Updating an installation of the Windows installer in place (no network)."""

from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path
from typing import Any, ClassVar

import pytest

from editor_gui import self_update
from editor_gui.update_check import ReleaseAsset

_ISS = Path(__file__).resolve().parents[3] / "packaging" / "windows" / "xknx-editor.iss"
_PAYLOAD = b"installer bytes" * 1000


def _asset(sha256: str | None = None) -> ReleaseAsset:
    return ReleaseAsset(
        "XKNX-Editor-windows-installer-x64.exe",
        "https://example.org/XKNX-Editor-windows-installer-x64.exe",
        sha256,
        len(_PAYLOAD),
    )


class _Response(io.BytesIO):
    headers: ClassVar[dict[str, str]] = {"Content-Length": str(len(_PAYLOAD))}

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request: Any, timeout: float) -> _Response:
        return _Response(_PAYLOAD)

    monkeypatch.setattr(self_update.urllib.request, "urlopen", urlopen)


def test_the_app_id_is_the_one_of_the_installer():
    iss = _ISS.read_text(encoding="utf-8-sig")
    app_id = re.search(r"^AppId=\{(\{.+\})$", iss, re.M)
    assert app_id is not None
    assert app_id.group(1) == self_update.APP_ID


@pytest.mark.parametrize(
    ("machine", "name"),
    [
        ("AMD64", "XKNX-Editor-windows-installer-x64.exe"),
        ("x86_64", "XKNX-Editor-windows-installer-x64.exe"),
        ("ARM64", "XKNX-Editor-windows-installer-arm64.exe"),
    ],
)
def test_the_installer_matches_the_architecture(machine: str, name: str):
    assets = (
        ReleaseAsset(
            "XKNX-Editor-windows-installer-arm64.exe", "https://example.org/a"
        ),
        ReleaseAsset("XKNX-Editor-windows-installer-x64.exe", "https://example.org/x"),
        ReleaseAsset("XKNX-Editor-windows-portable-x64.zip", "https://example.org/z"),
    )
    picked = self_update.pick_installer(assets, machine)
    assert picked is not None and picked.name == name


def test_a_release_without_installer_has_none():
    assets = (ReleaseAsset("XKNX-Editor-windows.zip", "https://example.org/z"),)
    assert self_update.pick_installer(assets, "AMD64") is None


def test_a_source_run_is_not_an_installation():
    assert self_update.installed_scope() is None


def test_the_update_runs_silently_in_the_same_scope_and_reopens_the_project():
    assert self_update.installer_arguments("user", r"C:\Projekte\Haus.xknx") == [
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/CURRENTUSER",
        "/RELAUNCH=1",
        r"/OPEN=C:\Projekte\Haus.xknx",
    ]
    args = self_update.installer_arguments("machine", None)
    assert "/ALLUSERS" in args and not any(a.startswith("/OPEN=") for a in args)


@pytest.mark.usefixtures("served")
def test_the_download_is_checked_against_its_digest(tmp_path: Path):
    target = tmp_path / "setup.exe"
    seen: list[tuple[int, int]] = []
    digest = hashlib.sha256(_PAYLOAD).hexdigest().upper()
    self_update.download(
        _asset(digest), target, lambda done, total: seen.append((done, total))
    )
    assert target.read_bytes() == _PAYLOAD
    assert seen[-1] == (len(_PAYLOAD), len(_PAYLOAD))


@pytest.mark.usefixtures("served")
def test_a_wrong_digest_leaves_no_file(tmp_path: Path):
    target = tmp_path / "setup.exe"
    with pytest.raises(ValueError, match="SHA-256"):
        self_update.download(_asset("00" * 32), target)
    assert not target.exists()


def test_only_https_is_downloaded(tmp_path: Path):
    asset = ReleaseAsset("x.exe", "http://example.org/x.exe")
    with pytest.raises(ValueError):
        self_update.download(asset, tmp_path / "x.exe")
