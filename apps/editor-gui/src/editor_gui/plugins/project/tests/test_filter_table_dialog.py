"""The coupler filter-table dialog: its CSV export and a headless render."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui.plugins.project.service import CouplerFilterTable, FilterTableEntry
from editor_gui.plugins.project.strings import S
from editor_gui.plugins.project.ui.filter_table import (
    DIALOG_WIDTH,
    FilterTableDialog,
    _csv,
    _safe_cell,
)
from editor_gui.widgets.dpt_value import dpt_label


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1000, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _table() -> CouplerFilterTable:
    return CouplerFilterTable(
        "1.1.0",
        "Line Coupler",
        [
            FilterTableEntry(0x0801, "1/0/1", "Shared", None),
            FilterTableEntry(0x0A03, "1/2/3", "Switch", "DPST-1-1"),
        ],
    )


def test_csv_has_a_header_and_one_row_per_address() -> None:
    lines = _csv(_table()).splitlines()
    assert lines[0] == "Group Address,Name,Data Type"
    assert lines[1] == "1/0/1,Shared,"
    assert lines[2] == f"1/2/3,Switch,{dpt_label('DPST-1-1')}"
    assert len(lines) == 3


def test_csv_neutralises_formula_injection_in_names() -> None:
    table = CouplerFilterTable(
        "1.1.0",
        "Coupler",
        [FilterTableEntry(0x0801, "1/0/1", "=1+1", None)],
    )
    # A name a spreadsheet would run as a formula is prefixed with an apostrophe.
    assert _csv(table).splitlines()[1] == "1/0/1,'=1+1,"


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@", "\t", "\r", "\n"])
def test_safe_cell_prefixes_every_formula_trigger(prefix: str) -> None:
    assert _safe_cell(f"{prefix}x") == f"'{prefix}x"


def test_safe_cell_leaves_ordinary_text() -> None:
    assert _safe_cell("Kitchen Light") == "Kitchen Light"
    assert _safe_cell("1/2/3") == "1/2/3"
    assert _safe_cell("") == ""


def test_open_ignores_a_device_that_is_no_coupler() -> None:
    dialog = FilterTableDialog(lambda _n: None)
    dialog.open(5)
    assert dialog._table is None  # pyright: ignore[reportPrivateUsage]
    assert dialog._pending is None  # pyright: ignore[reportPrivateUsage]


def test_open_takes_the_table_of_a_coupler() -> None:
    table = _table()
    dialog = FilterTableDialog(lambda _n: table)
    dialog.open(1)
    assert dialog._table is table  # pyright: ignore[reportPrivateUsage]
    assert dialog._pending == 1  # pyright: ignore[reportPrivateUsage]


@pytest.mark.usefixtures("context")
def test_render_does_not_crash() -> None:
    dialog = FilterTableDialog(lambda _n: _table())
    dialog.open(1)
    for _ in range(4):
        imgui.new_frame()
        imgui.set_next_window_pos(imgui.ImVec2(0, 0))
        imgui.set_next_window_size(imgui.ImVec2(900, 700))
        imgui.begin("Host")
        dialog.render()
        imgui.end()
        imgui.end_frame()


def _dialog_width(table: CouplerFilterTable, frames: int = 12) -> float:
    """The width the dialog of ``table`` has after ``frames`` frames."""
    dialog = FilterTableDialog(lambda _n: table)
    dialog.open(1)
    for _ in range(frames):
        imgui.new_frame()
        dialog.render()
        imgui.end_frame()
    window = internal.find_window_by_name(S.FILTER_TABLE_TITLE)
    return window.size.x


def _entries(name: str) -> list[FilterTableEntry]:
    return [
        FilterTableEntry(0x0800 + i, f"1/0/{i}", f"{name} {i}", "DPST-1-1")
        for i in range(100)
    ]


@pytest.mark.usefixtures("context")
def test_the_width_does_not_follow_the_names() -> None:
    """The dialog keeps its width whatever the rows hold (#388: it took the width of the rows in
    view, and changed it while the table scrolled); long names are clipped in the table."""
    short = _dialog_width(CouplerFilterTable("1.1.0", "Coupler", _entries("Hall")))
    long = _dialog_width(
        CouplerFilterTable("1.1.0", "Coupler", _entries("Hall.Workshop.Gate " * 12))
    )
    assert short == long == DIALOG_WIDTH


@pytest.mark.usefixtures("context")
def test_a_narrow_screen_gets_a_narrower_dialog() -> None:
    imgui.get_io().display_size = imgui.ImVec2(360, 640)
    table = CouplerFilterTable("1.1.0", "Coupler", _entries("Hall.Workshop " * 12))
    assert _dialog_width(table) <= 360
