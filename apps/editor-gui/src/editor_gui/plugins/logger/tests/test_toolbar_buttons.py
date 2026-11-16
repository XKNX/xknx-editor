"""The Log panel's copy and clear buttons are off while there is nothing to copy or clear."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.logger import ui
from editor_gui.plugins.logger.service import LogRecord
from editor_gui.plugins.logger.ui import LogPanel
from editor_gui.widgets.icon_button import ICON_CLEAR, ICON_COPY


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _buttons(
    monkeypatch: pytest.MonkeyPatch, records: list[LogRecord]
) -> dict[str, bool]:
    """Whether each toolbar icon button is enabled with ``records`` in the log."""
    enabled: dict[str, bool] = {}

    def button(icon: str, _str_id: str, _tooltip: str, **options: Any) -> bool:
        enabled[icon] = options.get("enabled", True)
        return False

    monkeypatch.setattr(ui, "icon_button", button)
    panel = LogPanel(lambda: records, lambda: len(records), lambda: None)
    imgui.new_frame()
    imgui.begin("Logs")
    panel._render_toolbar()  # pyright: ignore[reportPrivateUsage]
    imgui.end()
    imgui.end_frame()
    return enabled


@pytest.mark.usefixtures("context")
def test_copy_and_clear_are_off_for_an_empty_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _buttons(monkeypatch, []) == {ICON_COPY: False, ICON_CLEAR: False}


@pytest.mark.usefixtures("context")
def test_copy_and_clear_are_on_with_records(monkeypatch: pytest.MonkeyPatch) -> None:
    record = LogRecord(timestamp=0.0, level="info", plugin="test", event="started")
    assert _buttons(monkeypatch, [record]) == {ICON_COPY: True, ICON_CLEAR: True}
