"""Links to report a bug or suggest a feature, with what the editor knows about itself filled in.

The repository's issue forms (``.github/ISSUE_TEMPLATE``) take the text fields from the URL by
their ``id``: the version, the operating system and an environment summary. GitHub fills no
drop-down from the URL, so the environment repeats the installation for the reporter to pick.
The values stay English: they are read on GitHub, whatever language the editor shows.
"""

from __future__ import annotations

import os
import platform
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlencode

from editor_gui import __version__, host, self_update, strings, web

REPO_URL = "https://github.com/XKNX/xknx-editor"
ISSUES_URL = f"{REPO_URL}/issues"
_BUG_REPORT_TEMPLATE = "bug_report.yml"
_FEATURE_REQUEST_TEMPLATE = "feature_request.yml"

# The installation options of the bug report form.
WINDOWS_INSTALLER_X64 = "Windows installer, x64"
WINDOWS_INSTALLER_ARM64 = "Windows installer, ARM64"
WINDOWS_PORTABLE_X64 = "Windows portable, x64"
WINDOWS_PORTABLE_X64_SOFTWARE_GL = "Windows portable, x64, software OpenGL"
WINDOWS_PORTABLE_ARM64 = "Windows portable, ARM64"
MACOS = "macOS"
LINUX_APPIMAGE = "Linux AppImage"
LINUX_TAR = "Linux tar.gz"
DOCKER = "Docker / Unraid"
HOME_ASSISTANT = "Home Assistant"
FROM_SOURCE = "From source"

# The "used through" options of the bug report form.
USED_IN_WINDOW = "The editor's window"
USED_IN_BROWSER = "The browser (web interface)"


def _in_container() -> bool:
    # Docker writes /.dockerenv; the editor's image also sets XKNX_KEYEXTRACT_DIR.
    return Path("/.dockerenv").exists() or "XKNX_KEYEXTRACT_DIR" in os.environ


def _software_gl() -> bool:
    # Set by the start of a build that brings software OpenGL (tools/desktop_entry.py).
    return os.environ.get("XKNX_SOFTWARE_GL") == "1"


def installation() -> str:
    """How the editor runs, as the bug report form's installation option names it."""
    if host.home_assistant():
        return HOME_ASSISTANT
    if _in_container():
        return DOCKER
    if not getattr(sys, "frozen", False):
        return FROM_SOURCE
    arm = platform.machine().upper() in ("ARM64", "AARCH64")
    if sys.platform == "win32":
        if self_update.installed_scope() is not None:
            return WINDOWS_INSTALLER_ARM64 if arm else WINDOWS_INSTALLER_X64
        if arm:
            return WINDOWS_PORTABLE_ARM64
        return (
            WINDOWS_PORTABLE_X64_SOFTWARE_GL if _software_gl() else WINDOWS_PORTABLE_X64
        )
    if sys.platform == "darwin":
        return MACOS
    return LINUX_APPIMAGE if "APPIMAGE" in os.environ else LINUX_TAR


def operating_system() -> str:
    """The operating system with its version and architecture, e.g. "Windows 11 (10.0.26200), AMD64"."""
    machine = platform.machine()
    if sys.platform == "win32":
        return f"Windows {platform.release()} ({platform.version()}), {machine}"
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]}, {machine}"
    try:
        name = platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
    except OSError:
        name = "Linux"
    text = f"{name}, kernel {platform.release()}, {machine}"
    if _in_container():
        # the host and the network mode are not visible from inside the container
        text += ", in a container - host and network mode: "
    return text


def environment() -> str:
    """What else the editor knows about where it runs, one fact per line."""
    try:
        from importlib.metadata import version

        xknx = version("xknx")
    except Exception:
        xknx = "unknown"
    lines = [
        f"Installation: {installation()}",
        f"Used through: {USED_IN_BROWSER if web.active_hub is not None else USED_IN_WINDOW}",
        f"Interface language: {strings.get_locale()}",
        f"Python: {platform.python_version()}",
        f"xknx: {xknx}",
    ]
    if _software_gl():
        lines.append("Software OpenGL: yes")
    return "\n".join(lines)


def _new_issue(template: str, fields: Mapping[str, str]) -> str:
    return f"{ISSUES_URL}/new?{urlencode({'template': template, **fields})}"


def bug_report_url() -> str:
    """A new bug report with the version, the operating system and the environment filled in."""
    return _new_issue(
        _BUG_REPORT_TEMPLATE,
        {
            "version": __version__,
            "os": operating_system(),
            "environment": environment(),
        },
    )


def feature_request_url() -> str:
    """A new feature request with the version filled in."""
    return _new_issue(_FEATURE_REQUEST_TEMPLATE, {"version": __version__})
