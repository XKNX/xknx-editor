"""Update an app installed by the Windows installer in place.

The release's installer for this architecture is downloaded, checked against the SHA-256 digest
GitHub publishes for the asset, and run silently. It keeps the installation's folder and options,
asks the closing app's files to be released, and starts the app again with the project that was
open (see packaging/windows/xknx-editor.iss). A portable or source run, and other operating
systems, keep opening the release page instead.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from editor_gui.update_check import ReleaseAsset

# The installer's AppId (never changes); its uninstall key tells a per-user from an all-users
# installation.
APP_ID = "{F249E002-17F2-4361-B61D-033E8DADE666}"
_UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}_is1"
INSTALLER_ASSET = "XKNX-Editor-windows-installer-{arch}.exe"


def installer_asset_name(machine: str) -> str:
    """The installer asset for ``platform.machine()``."""
    arch = "arm64" if machine.upper() in ("ARM64", "AARCH64") else "x64"
    return INSTALLER_ASSET.format(arch=arch)


def pick_installer(
    assets: tuple[ReleaseAsset, ...], machine: str
) -> ReleaseAsset | None:
    """The release's installer for this architecture, if it has one."""
    name = installer_asset_name(machine)
    return next((asset for asset in assets if asset.name == name), None)


def installed_scope() -> str | None:
    """``"user"`` or ``"machine"`` when this app runs from an installation of the Windows
    installer, else ``None`` (portable ZIP, source run, other operating systems)."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return None
    app_dir = Path(sys.executable).parent
    if not (app_dir / "unins000.exe").exists():
        return None
    import winreg

    for scope, hive in (
        ("user", winreg.HKEY_CURRENT_USER),
        ("machine", winreg.HKEY_LOCAL_MACHINE),
    ):
        try:
            with winreg.OpenKey(hive, _UNINSTALL_KEY) as key:
                location, _kind = winreg.QueryValueEx(key, "InstallLocation")
        except OSError:
            continue
        if isinstance(location, str) and _same_dir(Path(location), app_dir):
            return scope
    return None


def _same_dir(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def download(
    asset: ReleaseAsset,
    target: Path,
    progress: Callable[[int, int], None] | None = None,
    *,
    timeout: float = 60.0,
) -> None:
    """Download ``asset`` to ``target`` and check its SHA-256 digest when GitHub has one.
    ``progress(done, total)`` is called while it runs; ``total`` is 0 when unknown. A failed
    download or digest leaves no file behind."""
    if urlparse(asset.url).scheme != "https":
        raise ValueError("only https downloads are supported")
    digest = hashlib.sha256()
    request = urllib.request.Request(asset.url, headers={"User-Agent": "xknx-editor"})
    try:
        with (
            urllib.request.urlopen(request, timeout=timeout) as response,
            target.open("wb") as out,
        ):
            total = int(response.headers.get("Content-Length") or asset.size or 0)
            done = 0
            while block := response.read(256 * 1024):
                out.write(block)
                digest.update(block)
                done += len(block)
                if progress is not None:
                    progress(done, total)
        if asset.sha256 is not None and digest.hexdigest() != asset.sha256.lower():
            raise ValueError(
                "the downloaded installer does not match its SHA-256 digest"
            )
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def installer_arguments(scope: str, open_project: str | None) -> list[str]:
    """Command line of a silent update of the installation ``scope``, restarting the app with
    ``open_project`` when one is given."""
    args = [
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        "/ALLUSERS" if scope == "machine" else "/CURRENTUSER",
        "/RELAUNCH=1",
    ]
    if open_project:
        args.append(f"/OPEN={open_project}")
    return args


def start_installer(installer: Path, args: list[str]) -> None:
    """Start the installer detached from the app, which exits right after."""
    if sys.platform != "win32":
        raise OSError("the installer runs on Windows only")
    subprocess.Popen(
        [str(installer), *args],
        close_fds=True,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )
