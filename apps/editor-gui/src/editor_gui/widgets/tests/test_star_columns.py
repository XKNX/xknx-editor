"""Percentage columns in whole pixels as WPF rounds star columns: of several equal columns, the
ones WPF's sort leaves last are a pixel narrower (the HF-Sensor page of the OpenKNX
RaumController at 100 %)."""

from __future__ import annotations

import pytest

from editor_gui.widgets.star_columns import (
    _sorted_indices,  # pyright: ignore[reportPrivateUsage]
    star_widths,
)


def _starts(weights: list[float], scale: float = 1.0) -> list[float]:
    starts, x = [], 0.0
    for width in star_widths(weights, 5.32, scale):
        starts.append(x)
        x += width
    return starts


def test_twelve_percent_columns_on_whole_pixels() -> None:
    # Trigger and Halten: 25 %, then 16 columns of 12 %; the fields at 211, 275 ... on the page
    # (15 px in), two of the first eight columns a pixel narrower.
    starts = _starts([25.0] + [12.0] * 16)
    assert [s + 15 for s in starts[2:10]] == [211, 275, 339, 403, 467, 531, 594, 658]


def test_columns_of_different_weights() -> None:
    # Kopiere / Berechne: drop-downs at 148 and 489, a button's cell at 766.
    assert [s + 15 for s in _starts([25.0, 52.0, 12.0, 52.0, 14.0])] == [
        15,
        148,
        425,
        489,
        766,
    ]
    # Stichprobe: buttons in cells at 143, 271 and 398.
    assert [s + 15 for s in _starts([24.0] * 5)][1:4] == [143, 271, 398]


def test_the_widths_add_up_to_the_rounded_grid() -> None:
    for weights in ([25.0] + [12.0] * 16, [24.0] * 5, [33.3] * 3, [1.0, 99.0]):
        widths = star_widths(weights, 5.32)
        assert all(w == int(w) for w in widths)
        assert sum(widths) == round(sum(weights) * 5.32)


def test_at_a_larger_scale_whole_pixels_of_the_screen() -> None:
    widths = star_widths([24.0] * 5, 5.32, 1.25)
    assert all(w == int(w) for w in widths)
    assert sum(widths) == pytest.approx(round(120 * 5.32 * 1.25))


def test_equal_keys_keep_the_order_dotnet_leaves_them_in() -> None:
    # Up to 16 keys are sorted by insertion: equal ones stay in order.
    assert _sorted_indices([1.0, 0.5, 1.0, 0.5]) == [1, 3, 0, 2]
    # More are partitioned around a pivot first, which moves equal keys around.
    order = _sorted_indices([0.0] + [0.16] * 16)
    assert order[0] == 0 and sorted(order) == list(range(17))
    assert order[1:] != list(range(1, 17))
