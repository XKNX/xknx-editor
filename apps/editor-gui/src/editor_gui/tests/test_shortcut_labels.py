"""Shortcut labels as the platform writes them: Ctrl on Windows/Linux, Cmd on a Mac (the browser's
platform in the browser view)."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from editor_gui import web
from editor_gui.strings import S, shortcut


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("win32", "Ctrl+E"), ("linux", "Ctrl+E"), ("darwin", "Cmd+E")],
)
def test_native_labels(
    monkeypatch: pytest.MonkeyPatch, platform: str, expected: str
) -> None:
    monkeypatch.setattr(web, "active_hub", None)
    monkeypatch.setattr(sys, "platform", platform)
    label = S.SHORTCUT_EXPORT
    assert label == expected


@pytest.mark.parametrize("server", ["win32", "darwin"])
@pytest.mark.parametrize(
    ("mac", "expected"), [(False, "Ctrl+Y"), (True, "Cmd+Shift+Z")]
)
def test_browser_labels_follow_the_browser(
    monkeypatch: pytest.MonkeyPatch, server: str, mac: bool, expected: str
) -> None:
    monkeypatch.setattr(web, "active_hub", SimpleNamespace(client_mac=mac))
    monkeypatch.setattr(sys, "platform", server)
    label = S.SHORTCUT_REDO
    assert label == expected
    assert shortcut("0").endswith("+0")
