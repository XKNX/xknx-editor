"""The Feedback menu's links fill in the repository's issue forms with what the editor knows."""

from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from editor_gui import __version__, feedback, host, self_update, web


def _forms_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        forms = parent / ".github" / "ISSUE_TEMPLATE"
        if forms.is_dir():
            return forms
    raise AssertionError("no .github/ISSUE_TEMPLATE above the tests")


def _form(name: str) -> dict[str, Any]:
    return yaml.safe_load((_forms_dir() / name).read_text(encoding="utf-8"))


def _field(form: dict[str, Any], field_id: str) -> dict[str, Any]:
    return next(f for f in form["body"] if f.get("id") == field_id)


def _query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


@pytest.fixture
def clean(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No container, no Home Assistant, a frozen build, no special environment."""
    monkeypatch.setattr(host, "home_assistant", lambda: False)
    monkeypatch.setattr(feedback, "_in_container", lambda: False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    for name in ("APPIMAGE", "XKNX_SOFTWARE_GL", "XKNX_KEYEXTRACT_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(web, "active_hub", None)
    return monkeypatch


def test_every_installation_is_an_option_of_the_bug_report() -> None:
    options = _field(_form("bug_report.yml"), "installation")["attributes"]["options"]
    values = [
        feedback.WINDOWS_INSTALLER_X64,
        feedback.WINDOWS_INSTALLER_ARM64,
        feedback.WINDOWS_PORTABLE_X64,
        feedback.WINDOWS_PORTABLE_X64_SOFTWARE_GL,
        feedback.WINDOWS_PORTABLE_ARM64,
        feedback.MACOS,
        feedback.LINUX_APPIMAGE,
        feedback.LINUX_TAR,
        feedback.DOCKER,
        feedback.HOME_ASSISTANT,
        feedback.FROM_SOURCE,
    ]
    assert sorted(values) == sorted(options)


def test_the_ways_of_use_are_options_of_the_bug_report() -> None:
    options = _field(_form("bug_report.yml"), "usage")["attributes"]["options"]
    assert feedback.USED_IN_WINDOW in options
    assert feedback.USED_IN_BROWSER in options


@pytest.mark.parametrize(
    ("machine", "scope", "software_gl", "expected"),
    [
        ("AMD64", "user", False, feedback.WINDOWS_INSTALLER_X64),
        ("ARM64", "machine", False, feedback.WINDOWS_INSTALLER_ARM64),
        ("AMD64", "user", True, feedback.WINDOWS_INSTALLER_X64),
        ("AMD64", None, False, feedback.WINDOWS_PORTABLE_X64),
        ("AMD64", None, True, feedback.WINDOWS_PORTABLE_X64_SOFTWARE_GL),
        ("ARM64", None, False, feedback.WINDOWS_PORTABLE_ARM64),
    ],
)
def test_windows_installation(
    clean: pytest.MonkeyPatch,
    machine: str,
    scope: str | None,
    software_gl: bool,
    expected: str,
) -> None:
    clean.setattr(sys, "platform", "win32")
    clean.setattr(platform, "machine", lambda: machine)
    clean.setattr(self_update, "installed_scope", lambda: scope)
    if software_gl:
        clean.setenv("XKNX_SOFTWARE_GL", "1")
    assert feedback.installation() == expected


def test_macos_and_linux_installations(clean: pytest.MonkeyPatch) -> None:
    clean.setattr(sys, "platform", "darwin")
    assert feedback.installation() == feedback.MACOS
    clean.setattr(sys, "platform", "linux")
    assert feedback.installation() == feedback.LINUX_TAR
    clean.setenv("APPIMAGE", "/home/user/XKNX-Editor-x86_64.AppImage")
    assert feedback.installation() == feedback.LINUX_APPIMAGE


def test_hosted_and_source_installations(clean: pytest.MonkeyPatch) -> None:
    clean.setattr(sys, "frozen", False)
    assert feedback.installation() == feedback.FROM_SOURCE
    clean.setattr(feedback, "_in_container", lambda: True)
    assert feedback.installation() == feedback.DOCKER
    clean.setattr(host, "home_assistant", lambda: True)
    assert feedback.installation() == feedback.HOME_ASSISTANT


def test_a_container_is_told_by_the_image(clean: pytest.MonkeyPatch) -> None:
    clean.undo()
    clean.delenv("XKNX_KEYEXTRACT_DIR", raising=False)
    in_docker = Path("/.dockerenv").exists()
    assert feedback._in_container() is in_docker  # pyright: ignore[reportPrivateUsage]
    clean.setenv("XKNX_KEYEXTRACT_DIR", "/opt/xknx-keyextract")
    assert feedback._in_container() is True  # pyright: ignore[reportPrivateUsage]


def test_operating_system(clean: pytest.MonkeyPatch) -> None:
    clean.setattr(platform, "machine", lambda: "AMD64")
    clean.setattr(sys, "platform", "win32")
    clean.setattr(platform, "release", lambda: "11")
    clean.setattr(platform, "version", lambda: "10.0.26200")
    assert feedback.operating_system() == "Windows 11 (10.0.26200), AMD64"

    clean.setattr(sys, "platform", "darwin")
    clean.setattr(platform, "machine", lambda: "arm64")
    clean.setattr(platform, "mac_ver", lambda: ("15.1", ("", "", ""), "arm64"))
    assert feedback.operating_system() == "macOS 15.1, arm64"

    clean.setattr(sys, "platform", "linux")
    clean.setattr(platform, "machine", lambda: "x86_64")
    clean.setattr(platform, "release", lambda: "6.8.0")
    clean.setattr(
        platform,
        "freedesktop_os_release",
        lambda: {"PRETTY_NAME": "Ubuntu 24.04.1 LTS"},
        raising=False,
    )
    assert feedback.operating_system() == "Ubuntu 24.04.1 LTS, kernel 6.8.0, x86_64"
    clean.setattr(feedback, "_in_container", lambda: True)
    assert feedback.operating_system().endswith(
        "in a container - host and network mode: "
    )

    def no_release() -> dict[str, str]:
        raise OSError("no os-release")

    clean.setattr(platform, "freedesktop_os_release", no_release, raising=False)
    clean.setattr(feedback, "_in_container", lambda: False)
    assert feedback.operating_system() == "Linux, kernel 6.8.0, x86_64"


def test_environment(clean: pytest.MonkeyPatch) -> None:
    clean.setattr(sys, "frozen", False)
    lines = feedback.environment().splitlines()
    assert lines[0] == f"Installation: {feedback.FROM_SOURCE}"
    assert lines[1] == f"Used through: {feedback.USED_IN_WINDOW}"
    assert lines[2].startswith("Interface language: ")
    assert lines[3] == f"Python: {platform.python_version()}"
    assert lines[4].startswith("xknx: ") and lines[4] != "xknx: unknown"
    assert len(lines) == 5

    clean.setattr(web, "active_hub", object())
    clean.setenv("XKNX_SOFTWARE_GL", "1")
    lines = feedback.environment().splitlines()
    assert lines[1] == f"Used through: {feedback.USED_IN_BROWSER}"
    assert lines[-1] == "Software OpenGL: yes"


def test_bug_report_url_fills_the_forms_text_fields(clean: pytest.MonkeyPatch) -> None:
    url = feedback.bug_report_url()
    assert url.startswith("https://github.com/XKNX/xknx-editor/issues/new?")
    query = _query(url)
    assert query.pop("template") == "bug_report.yml"
    assert query == {
        "version": __version__,
        "os": feedback.operating_system(),
        "environment": feedback.environment(),
    }
    form = _form("bug_report.yml")
    for field_id in query:
        # GitHub fills only inputs and text areas from the URL
        assert _field(form, field_id)["type"] in ("input", "textarea")


def test_feature_request_url(clean: pytest.MonkeyPatch) -> None:
    query = _query(feedback.feature_request_url())
    assert query == {"template": "feature_request.yml", "version": __version__}
    assert _field(_form("feature_request.yml"), "version")["type"] == "input"


def test_the_forms_set_the_issue_type() -> None:
    assert _form("bug_report.yml")["type"] == "Bug"
    assert _form("feature_request.yml")["type"] == "Feature"
    assert "labels" not in _form("bug_report.yml")


def test_the_issues_list() -> None:
    assert feedback.ISSUES_URL == "https://github.com/XKNX/xknx-editor/issues"
    assert os.path.basename(feedback.ISSUES_URL) == "issues"
