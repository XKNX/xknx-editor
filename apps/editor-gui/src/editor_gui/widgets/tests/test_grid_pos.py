"""Cell positions in a grid block: beyond the declared rows/columns they land in the last one."""

from editor_gui.widgets.parameter_widgets import (
    _grid_pos,
    _keep_column_starts,
    _whole_pixel_starts,
)


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


def test_narrow_column_keeps_the_next_columns_at_their_start() -> None:
    # 1 % and 44 % of 540 px: the first column is widened to the minimum, the second gives it back.
    assert _keep_column_starts([5.4, 237.6, 81.0], 8.0) == [8.0, 235.0, 81.0]


def test_wide_columns_are_kept() -> None:
    assert _keep_column_starts([10.8, 232.2, None, 81.0], 8.0) == [
        10.8,
        232.2,
        None,
        81.0,
    ]


def test_columns_start_on_their_rounded_declared_start() -> None:
    # 2 %, 20 %, 40 % of 532 px: cut down one by one, the third column would start at 116.
    assert _whole_pixel_starts([10.64, 106.4, 212.8]) == [11.0, 106.0, 213.0]
    # A stretching column starts the count again.
    assert _whole_pixel_starts([10.4, None, 10.4]) == [10.0, None, 10.0]
