"""Tables of data keep their ids until the default layout is restored; then every table gets a
new id, which has no stored column settings, and the count of resets survives a restart."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui
from imgui_bundle.imgui import internal

from editor_gui import settings
from editor_gui.widgets import tables


@pytest.fixture(autouse=True)
def _tmp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, "config_dir", lambda: tmp_path)
    tables._generation.clear()  # pyright: ignore[reportPrivateUsage]
    yield
    tables._generation.clear()  # pyright: ignore[reportPrivateUsage]


def test_ids_stay_until_the_first_reset() -> None:
    assert tables.data_table_id("##logs") == "##logs"


def test_a_reset_gives_every_table_a_new_id() -> None:
    tables.reset_all_tables()
    assert tables.data_table_id("##logs") == "##logs#1"
    tables.reset_all_tables()
    assert tables.data_table_id("##logs") == "##logs#2"


def test_the_resets_are_remembered() -> None:
    tables.reset_all_tables()
    tables._generation.clear()  # pyright: ignore[reportPrivateUsage]
    assert tables.data_table_id("##logs") == "##logs#1"


@pytest.fixture
def frame() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    imgui.new_frame()
    imgui.begin("window")
    yield
    imgui.end()
    imgui.end_frame()
    imgui.destroy_context(ctx)


def _header_freeze(flags: int, columns_frozen: int = 0) -> tuple[int, int]:
    """The (columns, rows) a table keeps at the top/left after its header row."""
    assert tables.begin_data_table("##t", 2, flags, imgui.ImVec2(0.0, 200.0))
    if columns_frozen:
        imgui.table_setup_scroll_freeze(columns_frozen, 0)
    imgui.table_setup_column("A")
    imgui.table_setup_column("B")
    tables.data_table_headers()
    table = internal.get_current_table()
    frozen = (table.freeze_columns_request, table.freeze_rows_request)
    imgui.end_table()
    return frozen


@pytest.mark.usefixtures("frame")
def test_the_header_of_a_scrolling_table_stays() -> None:
    assert _header_freeze(imgui.TableFlags_.scroll_y) == (0, 1)
    # Columns the caller froze stay frozen.
    scroll_both = imgui.TableFlags_.scroll_y | imgui.TableFlags_.scroll_x
    assert _header_freeze(scroll_both, columns_frozen=1) == (1, 1)
    # A table without its own vertical scrolling has nothing to freeze.
    assert _header_freeze(imgui.TableFlags_.none) == (0, 0)
