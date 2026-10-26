"""At a start each section shows its first tab: the windows appearing take the focus one after
another, so without that the last one created (the Project Log) would be shown in its section."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from editor_gui.main import create_dockable_windows


def _panel(name: str, dock: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, label=name, dock=dock, render=lambda: None)


def test_each_section_starts_on_its_first_tab() -> None:
    app = SimpleNamespace(
        get_all_panels=lambda: [
            _panel("buildings", "LeftSpace"),
            _panel("topology", "LeftSpace"),
            _panel("editor", "MainDockSpace"),
            _panel("monitor", "BottomSpace"),
            _panel("timeline", "BottomSpace"),
            _panel("project_info", "BottomSpace"),
            _panel("project_log", "BottomSpace"),
        ]
    )
    windows = create_dockable_windows(cast(Any, app))
    focused = [
        w.label.split("###")[-1] for w in windows if w.focus_window_at_next_frame
    ]
    assert focused == ["buildings", "editor", "monitor"]
