"""A dialog's buttons stay inside it: equally wide where they fit, as wide as their labels where
they do not, and on a new row where not even that fits; the dismiss button at the right edge."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from imgui_bundle import imgui

from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _rects(
    width: float, *labels: str
) -> tuple[list[tuple[float, float, float, float]], float, float]:
    """The buttons' rectangles in the last of a few frames, and the dialog's content edges."""
    rects: list[tuple[float, float, float, float]] = []
    left = right = 0.0
    for frame in range(3):
        imgui.new_frame()
        if frame == 0:
            imgui.open_popup("Update Available")
        if begin_dialog("Update Available", width=width):
            rects.clear()
            dialog_buttons(*labels, rects=rects)
            window = imgui.get_window_pos().x
            left = window + imgui.get_style().window_padding.x
            right = (
                window + imgui.get_window_width() - imgui.get_style().window_padding.x
            )
            end_dialog()
        imgui.end_frame()
        imgui.render()
    return rects, left, right


@pytest.mark.usefixtures("context")
def test_buttons_that_fit_are_equally_wide() -> None:
    rects, _left, right = _rects(460, "OK", "Skip", "Later")
    assert len({w for _x, _y, w, _h in rects}) == 1
    # The dismiss button at the right edge.
    x, _y, w, _h = rects[-1]
    assert x + w == pytest.approx(right, abs=1.0)


@pytest.mark.usefixtures("context")
def test_long_labels_keep_the_buttons_inside_the_dialog() -> None:
    labels = ("Update installieren", "Diese Version überspringen", "Später")
    rects, left, right = _rects(480, *labels)
    assert all(left - 1 <= x and x + w <= right + 1 for x, _y, w, _h in rects)
    # Each as wide as it needs, on one row.
    assert len({w for _x, _y, w, _h in rects}) > 1
    assert len({y for _x, y, _w, _h in rects}) == 1
    # Narrower still, a button that does not fit starts a new row.
    rects, left, right = _rects(300, *labels)
    assert all(left - 1 <= x and x + w <= right + 1 for x, _y, w, _h in rects)
    assert len({y for _x, y, _w, _h in rects}) > 1
