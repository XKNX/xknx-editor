"""The Project Log tab only while the open project has a log (only an imported project has one):
otherwise it is hidden and left out of the menus, and it comes back with a project that has one."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from editor_gui.main import KnxGuiApp
from editor_gui.plugins.project.service import ProjectService, ProjectTrace


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    stored: dict[str, Any] = {}
    monkeypatch.setattr("editor_gui.main.load_settings", lambda name: dict(stored))
    monkeypatch.setattr(
        "editor_gui.main.save_settings", lambda name, data: stored.update(data)
    )
    return stored


@pytest.fixture
def tab(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    window = SimpleNamespace(
        label="Project Log###project_log",
        is_visible=True,
        include_in_view_menu=True,
        imgui_window_flags=0,
        dock_space_name="BottomSpace",
    )
    other = SimpleNamespace(
        label="Project###project_info",
        is_visible=True,
        include_in_view_menu=True,
        dock_space_name="BottomSpace",
    )
    elsewhere = SimpleNamespace(
        label="Editor###editor",
        is_visible=True,
        include_in_view_menu=True,
        dock_space_name="MainDockSpace",
    )
    params = SimpleNamespace(
        docking_params=SimpleNamespace(dockable_windows=[other, elsewhere, window])
    )
    # No imgui window is shown here, so no section has a selected tab.
    monkeypatch.setattr("editor_gui.main._selected_tab_window", lambda _names: None)
    monkeypatch.setattr("editor_gui.main.hello_imgui.get_runner_params", lambda: params)
    return window


def _app(has_log: bool | None, hidden: bool = False) -> SimpleNamespace:
    app = SimpleNamespace(
        _project_service=SimpleNamespace(has_project_log=lambda: has_log),
        _project_log_hidden=hidden,
    )
    app._set_project_log_hidden = lambda value: KnxGuiApp._set_project_log_hidden(
        app, value
    )  # type: ignore[arg-type]
    return app


def _sync(app: SimpleNamespace) -> None:
    KnxGuiApp._sync_project_log(app)  # type: ignore[arg-type]


def test_without_a_log_the_tab_is_hidden_and_out_of_the_menus(
    tab: SimpleNamespace, settings: dict[str, Any]
) -> None:
    app = _app(has_log=False)
    _sync(app)
    assert not tab.is_visible
    assert not tab.include_in_view_menu
    assert app._project_log_hidden
    assert settings["project_log_hidden"] is True


def test_it_comes_back_with_a_project_that_has_a_log(
    tab: SimpleNamespace, settings: dict[str, Any]
) -> None:
    tab.is_visible = False
    tab.include_in_view_menu = False
    app = _app(has_log=True, hidden=True)
    _sync(app)
    assert tab.is_visible
    assert tab.include_in_view_menu
    assert not app._project_log_hidden
    assert settings["project_log_hidden"] is False


def test_its_section_keeps_showing_its_tab_when_it_comes_back(
    tab: SimpleNamespace, settings: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """At a start the tab is hidden until the project is open; coming back, imgui would
    select it, so the section's tab is kept (the Monitor, not the Project Log)."""
    asked: list[list[str]] = []

    def selected(names: list[str]) -> str:
        asked.append(names)
        return "Monitor###monitor"

    monkeypatch.setattr("editor_gui.main._selected_tab_window", selected)
    monkeypatch.setattr("editor_gui.main.imgui.get_frame_count", lambda: 5)
    tab.is_visible = False
    app = _app(has_log=True, hidden=True)
    _sync(app)
    assert tab.is_visible
    # Only the tabs of its own section.
    assert asked == [["project_info"]]
    assert app._kept_tab == ("Monitor###monitor", 15)


def test_a_tab_the_user_closed_stays_closed(
    tab: SimpleNamespace, settings: dict[str, Any]
) -> None:
    tab.is_visible = False
    _sync(_app(has_log=True, hidden=False))
    assert not tab.is_visible
    assert tab.include_in_view_menu
    assert settings == {}


def test_nothing_changes_while_the_log_cannot_be_read(
    tab: SimpleNamespace, settings: dict[str, Any]
) -> None:
    _sync(_app(has_log=None))
    assert tab.is_visible and tab.include_in_view_menu
    assert settings == {}


def test_whether_a_project_has_a_log() -> None:
    service = SimpleNamespace(_io_lock=threading.RLock(), get_project_traces=list)
    assert ProjectService.has_project_log(service) is False  # type: ignore[arg-type]
    service.get_project_traces = lambda: [ProjectTrace("2026-01-01", "u", "c")]
    assert ProjectService.has_project_log(service) is True  # type: ignore[arg-type]

    def import_running() -> None:
        assert ProjectService.has_project_log(service) is None  # type: ignore[arg-type]

    with service._io_lock:
        worker = threading.Thread(target=import_running)
        worker.start()
        worker.join()
