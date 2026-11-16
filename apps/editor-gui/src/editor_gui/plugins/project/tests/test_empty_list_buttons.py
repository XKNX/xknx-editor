"""A button that copies, clears, exports or selects the entries of a list is off while the list
gives it nothing to do."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from imgui_bundle import imgui

from editor_gui.main import KnxGuiApp
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui import tools
from editor_gui.plugins.project.ui.group_addresses import has_group_addresses
from editor_gui.plugins.project.ui.mass_linker import MassLinkerPanel, _LogEntry
from editor_gui.widgets.icon_button import copy_button
from xknxeditor.namespaces.intermediate import GroupAddressStyle


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _disabled_buttons(
    monkeypatch: pytest.MonkeyPatch, draw: Callable[[], None]
) -> dict[str, bool]:
    """Every button ``draw`` shows, by its label, and whether it is off."""
    disabled: dict[str, bool] = {}
    button = imgui.button

    def record(label: str, *args: Any, **kwargs: Any) -> bool:
        pressed = button(label, *args, **kwargs)
        disabled[label] = bool(
            imgui.get_current_context().current_item_flags & imgui.ItemFlags_.disabled
        )
        return pressed

    monkeypatch.setattr(imgui, "button", record)
    imgui.new_frame()
    imgui.begin("Buttons")
    draw()
    imgui.end()
    imgui.end_frame()
    return disabled


def _mass_linker() -> MassLinkerPanel:
    return MassLinkerPanel(
        get_devices=list,
        get_range_tree=list,
        is_open=lambda: True,
        on_link_ga_co=lambda _pairs: (0, []),
        on_link_co_co=lambda _pairs: (0, []),
        get_selected_node_id=lambda: None,
        group_style=lambda: GroupAddressStyle.THREE_LEVEL,
    )


@pytest.mark.usefixtures("context")
def test_the_mass_linker_log_clears_only_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel = _mass_linker()
    draw = panel._render_log_tab  # pyright: ignore[reportPrivateUsage]
    assert _disabled_buttons(monkeypatch, draw)[S.ML_LOG_CLEAR]
    panel._log_entries.append(_LogEntry("12:00", "Linked"))  # pyright: ignore[reportPrivateUsage]
    assert not _disabled_buttons(monkeypatch, draw)[S.ML_LOG_CLEAR]


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize(
    ("shown", "selected", "all_off", "none_off"),
    [
        # Nothing shown (an empty list, or a filter that matches nothing): both off.
        (set(), set(), True, True),
        (set(), {1}, True, True),
        # Some of the shown checked: both do something.
        ({1, 2}, {1}, False, False),
        # None checked: only All; all checked: only None.
        ({1, 2}, set(), False, True),
        ({1, 2}, {1, 2, 3}, True, False),
    ],
)
def test_all_and_none_only_while_they_change_something(
    monkeypatch: pytest.MonkeyPatch,
    shown: set[int],
    selected: set[int],
    all_off: bool,
    none_off: bool,
) -> None:
    disabled = _disabled_buttons(
        monkeypatch,
        lambda: tools._select_all_none("list", shown, selected),  # pyright: ignore[reportPrivateUsage]
    )
    assert disabled[f"{S.TOOLS_SELECT_ALL}##list_all"] is all_off
    assert disabled[f"{S.TOOLS_SELECT_NONE}##list_none"] is none_off


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize(
    ("pressed", "after"),
    [
        (f"{S.TOOLS_SELECT_ALL}##list_all", {1, 2, 9}),
        (f"{S.TOOLS_SELECT_NONE}##list_none", {9}),
    ],
)
def test_all_and_none_change_the_shown_entries_only(
    monkeypatch: pytest.MonkeyPatch, pressed: str, after: set[int]
) -> None:
    # 9 is checked but filtered out: neither button touches it.
    selected = {1, 9}
    monkeypatch.setattr(
        imgui, "button", lambda label, *_args, **_kwargs: label == pressed
    )
    imgui.new_frame()
    imgui.begin("Buttons")
    tools._select_all_none("list", {1, 2}, selected)  # pyright: ignore[reportPrivateUsage]
    imgui.end()
    imgui.end_frame()
    assert selected == after


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize(("text", "off"), [("", True), ("1.1.1", False)])
def test_a_copy_button_is_off_without_text(
    monkeypatch: pytest.MonkeyPatch, text: str, off: bool
) -> None:
    disabled = _disabled_buttons(monkeypatch, lambda: copy_button("value", text))
    assert list(disabled.values()) == [off]


def _range(children: list[Any], addresses: int = 0) -> Any:
    return SimpleNamespace(
        children=children, group_addresses=[SimpleNamespace()] * addresses
    )


def test_group_addresses_are_found_at_any_depth() -> None:
    assert not has_group_addresses([])
    assert not has_group_addresses([_range([_range([])])])
    assert has_group_addresses([_range([]), _range([_range([], addresses=1)])])


@pytest.mark.parametrize(
    ("is_open", "loading", "addresses", "can"),
    [
        (False, False, 3, False),
        (True, False, 0, False),
        (True, False, 3, True),
        # While a project loads, its reads come back empty: the export may go on.
        (True, True, 0, True),
    ],
)
def test_the_group_address_export_needs_group_addresses(
    is_open: bool, loading: bool, addresses: int, can: bool
) -> None:
    service = SimpleNamespace(
        is_open=is_open, loading=loading, group_addresses=[object()] * addresses
    )
    stub = SimpleNamespace(_project_service=service)
    assert KnxGuiApp._can_export_ga(stub) is can  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]


def test_clear_cache_looks_at_the_cache_at_most_once_a_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from editor_gui import main

    looked: list[bool] = []
    answers = iter([False, True])

    def has_files() -> bool:
        looked.append(True)
        return next(answers)

    now = [100.0]
    monkeypatch.setattr(main, "cache_has_files", has_files)
    monkeypatch.setattr(main.time, "monotonic", lambda: now[0])
    stub = SimpleNamespace(_cache_has_files=True, _cache_checked=float("-inf"))
    holds = lambda: KnxGuiApp._cache_holds_files(stub)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]  # noqa: E731
    assert not holds()
    now[0] += 0.5
    assert not holds()
    assert len(looked) == 1
    now[0] += 0.6
    assert holds()
    assert len(looked) == 2
