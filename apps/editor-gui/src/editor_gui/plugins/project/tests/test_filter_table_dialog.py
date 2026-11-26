"""The coupler filter-table dialog: its CSV export and a headless render."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.plugins.project.service import CouplerFilterTable, FilterTableEntry
from editor_gui.plugins.project.ui.filter_table import FilterTableDialog, _csv
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
