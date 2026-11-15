"""The Log panel's first column is each record's level as an icon, as Health shows its kinds: an
"i" for info, the triangle for a warning, the cross for an error, a bug for a debug record."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.logger.service import LogRecord
from editor_gui.plugins.logger.ui import LogPanel
from editor_gui.theme import status_color
from editor_gui.widgets.icon_button import (
    ICON_DEBUG,
    ICON_ERROR,
    ICON_INFO,
    ICON_WARNING,
)


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _record(level: str, event: str) -> LogRecord:
    return LogRecord(timestamp=0.0, level=level, plugin="test", event=event)


def test_each_level_is_an_icon_in_the_first_column(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = [
        _record("debug", "traced"),
        _record("info", "started"),
        _record("warning", "unplugged"),
        _record("error", "failed"),
    ]
    panel = LogPanel(lambda: records, lambda: 1, lambda: None)
    # Every level shown, debug records too.
    panel._min_level_idx = 0  # pyright: ignore[reportPrivateUsage]
    drawn: list[tuple[str, str]] = []
    colored, disabled, text = imgui.text_colored, imgui.text_disabled, imgui.text

    def record_colored(color: imgui.ImVec4, value: str) -> None:
        colored(color, value)
        drawn.append((value, f"{color.x:.3f},{color.y:.3f},{color.z:.3f}"))

    def record_disabled(value: str) -> None:
        disabled(value)
        drawn.append((value, "disabled"))

    def record_text(value: str) -> None:
        text(value)
        drawn.append((value, "text"))

    monkeypatch.setattr(imgui, "text_colored", record_colored)
    monkeypatch.setattr(imgui, "text_disabled", record_disabled)
    monkeypatch.setattr(imgui, "text", record_text)
    imgui.new_frame()
    imgui.set_next_window_size(imgui.ImVec2(600, 400))
    imgui.begin("Log")
    panel.render()
    imgui.end()
    imgui.end_frame()

    def color(kind: str) -> str:
        c = status_color(kind)
        return f"{c.x:.3f},{c.y:.3f},{c.z:.3f}"

    icons = [
        entry
        for entry in drawn
        if entry[0] in (ICON_DEBUG, ICON_INFO, ICON_WARNING, ICON_ERROR)
    ]
    assert icons == [
        (ICON_DEBUG, "disabled"),
        (ICON_INFO, color("info")),
        (ICON_WARNING, color("warning")),
        (ICON_ERROR, color("error")),
    ]
    # No level as text any more.
    assert not any(value in ("DEBUG", "INFO", "WARNING", "ERROR") for value, _ in drawn)


def _toolbar_texts(panel: LogPanel, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    texts: list[str] = []
    disabled = imgui.text_disabled

    def record(value: str) -> None:
        disabled(value)
        texts.append(value)

    monkeypatch.setattr(imgui, "text_disabled", record)
    imgui.new_frame()
    imgui.set_next_window_size(imgui.ImVec2(600, 400))
    imgui.begin("Log")
    panel.render()
    imgui.end()
    imgui.end_frame()
    monkeypatch.setattr(imgui, "text_disabled", disabled)
    return texts


def test_the_count_says_how_many_are_shown_of_all(
    context: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = [
        _record("debug", "traced"),
        _record("info", "started"),
        _record("warning", "unplugged"),
        _record("error", "failed"),
    ]
    panel = LogPanel(lambda: records, lambda: 1, lambda: None)
    # The default level (Info) hides the debug record.
    assert _toolbar_texts(panel, monkeypatch)[0] == "3/4"
    panel._filter_text = "fail"  # pyright: ignore[reportPrivateUsage]
    assert _toolbar_texts(panel, monkeypatch)[0] == "1/4"
    # Nothing hidden: the number of all entries alone.
    panel._filter_text = ""  # pyright: ignore[reportPrivateUsage]
    panel._min_level_idx = 0  # pyright: ignore[reportPrivateUsage]
    assert _toolbar_texts(panel, monkeypatch)[0] == "4"
