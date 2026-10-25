"""Shortcut labels as the platform writes them: Ctrl on Windows/Linux, Cmd on a Mac (the browser's
platform in the browser view)."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from editor_gui import web
from editor_gui.plugins.bus_diagram.strings import S as BUS_DIAGRAM_S
from editor_gui.plugins.monitor.strings import S as MONITOR_S
from editor_gui.strings import S, get_locale, set_locale, shortcut
from editor_gui.widgets.strings import S as WIDGET_S


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


def test_labels_in_texts_and_the_comment_editor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web, "active_hub", None)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert "(Cmd+C)" in MONITOR_S.TOOL_COPY_HINT
    assert (WIDGET_S.SHORTCUT_BOLD, WIDGET_S.SHORTCUT_PASTE) == ("Cmd+B", "Cmd+V")
    for hint in (BUS_DIAGRAM_S.BUS_DIAGRAM_HINT, BUS_DIAGRAM_S.BUS_DIAGRAM_LINKS_HINT):
        assert "Cmd+scroll" in hint and "Ctrl" not in hint
    monkeypatch.setattr(sys, "platform", "win32")
    assert "(Ctrl+C)" in MONITOR_S.TOOL_COPY_HINT
    for hint in (BUS_DIAGRAM_S.BUS_DIAGRAM_HINT, BUS_DIAGRAM_S.BUS_DIAGRAM_LINKS_HINT):
        assert "Ctrl+scroll" in hint and "Cmd" not in hint


def test_ctrl_is_named_in_the_ui_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "active_hub", None)
    monkeypatch.setattr(sys, "platform", "win32")
    previous = get_locale()
    try:
        set_locale("de")
        assert (shortcut("E"), WIDGET_S.SHORTCUT_UNDERLINE) == ("Strg+E", "Strg+U")
        assert "(Strg+C)" in MONITOR_S.TOOL_COPY_HINT
        assert "Strg+Scrollen" in BUS_DIAGRAM_S.BUS_DIAGRAM_HINT
        monkeypatch.setattr(sys, "platform", "darwin")
        assert shortcut("E") == "Cmd+E"
    finally:
        set_locale(previous)
