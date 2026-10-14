"""Cell positions in a grid block: beyond the declared rows/columns they land in the last one."""

from editor_gui.widgets.parameter_widgets import _grid_pos


def test_cell_is_parsed() -> None:
    assert _grid_pos("2,3") == (2, 3)
    assert _grid_pos(None) is None
    assert _grid_pos("") is None
    assert _grid_pos("a,b") is None


def test_cell_beyond_the_declared_rows_lands_in_the_last_row() -> None:
    # A one-row grid whose cells name the channel number as their row (OpenKNX LED module).
    assert _grid_pos("4,2", rows=1, columns=4) == (1, 2)
    assert _grid_pos("1,1", rows=1, columns=4) == (1, 1)


def test_cell_beyond_the_declared_columns_lands_in_the_last_column() -> None:
    assert _grid_pos("1,6", rows=2, columns=4) == (1, 4)


def test_without_declared_rows_and_columns_the_cell_is_kept() -> None:
    assert _grid_pos("7,9") == (7, 9)
