"""A dialog's buttons sit in one row inside it: equally wide, the dismiss button at the right edge.
The dialog grows to that row; only on a screen too narrow for it are the buttons as wide as their
labels, and a button that does not fit the row starts a new one."""

from __future__ import annotations

import ast
import importlib
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from imgui_bundle import imgui

from editor_gui import strings
from editor_gui.languages import TRANSLATED
from editor_gui.widgets.dialog import begin_dialog, dialog_buttons, end_dialog

_Rect = tuple[float, float, float, float]
_SRC = Path(__file__).resolve().parents[2]


@pytest.fixture
def context() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(800, 600)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)


def _rects(
    *labels: str,
    width: float | None = None,
    body: Callable[[], None] | None = None,
    title: str = "Update Available",
) -> tuple[list[_Rect], float, float]:
    """The buttons' rectangles in the last of a few frames, and the dialog's content edges."""
    rects: list[_Rect] = []
    left = right = 0.0
    for frame in range(4):
        imgui.new_frame()
        if frame == 0:
            imgui.open_popup(title)
        if begin_dialog(title, width=width):
            if body is not None:
                body()
            rects.clear()
            dialog_buttons(*labels, rects=rects)
            window = imgui.get_window_pos().x
            padding = imgui.get_style().window_padding.x
            left = window + padding
            right = window + imgui.get_window_width() - padding
            end_dialog()
        imgui.end_frame()
        imgui.render()
    return rects, left, right


def _inside(rects: list[_Rect], left: float, right: float) -> bool:
    return all(left - 1 <= x and x + w <= right + 1 for x, _y, w, _h in rects)


def _one_row(rects: list[_Rect]) -> bool:
    return len({y for _x, y, _w, _h in rects}) == 1


@pytest.mark.usefixtures("context")
def test_buttons_that_fit_are_equally_wide() -> None:
    rects, _left, right = _rects("OK", "Skip", "Later", width=460)
    assert len({w for _x, _y, w, _h in rects}) == 1
    # The dismiss button at the right edge.
    x, _y, w, _h = rects[-1]
    assert x + w == pytest.approx(right, abs=1.0)


@pytest.mark.usefixtures("context")
def test_a_dialog_fitting_its_content_grows_to_its_buttons() -> None:
    # A short question and three buttons: the dialog is as wide as the row, not as the text.
    rects, left, right = _rects(
        "Ja", "Nein", "Abbrechen", body=lambda: imgui.text("Auch lesen?")
    )
    assert _one_row(rects) and _inside(rects, left, right)
    assert len({w for _x, _y, w, _h in rects}) == 1
    x, _y, w, _h = rects[-1]
    assert x + w == pytest.approx(right, abs=1.0)


@pytest.mark.usefixtures("context")
def test_a_dialog_of_a_fixed_width_grows_to_its_buttons() -> None:
    labels = ("Update installieren", "Diese Version überspringen", "Später")
    rects, left, right = _rects(*labels, width=300)
    assert _one_row(rects) and _inside(rects, left, right)
    assert right - left > 300


@pytest.mark.usefixtures("context")
def test_on_a_narrow_screen_the_buttons_take_more_rows() -> None:
    imgui.get_io().display_size = imgui.ImVec2(320, 600)
    labels = ("Update installieren", "Diese Version überspringen", "Später")
    rects, left, right = _rects(*labels)
    assert _inside(rects, left, right) and right <= 320
    # Each as wide as its label, on more than one row.
    assert len({w for _x, _y, w, _h in rects}) > 1
    assert not _one_row(rects)


def _label_sets() -> list[tuple[str, tuple[tuple[object, str], ...]]]:
    """Every row of buttons the editor's code passes as texts of its strings modules: where, and
    the strings object and property of each label."""
    found: list[tuple[str, tuple[tuple[object, str], ...]]] = []
    for path in sorted(_SRC.rglob("*.py")):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "dialog_buttons"
            for node in ast.walk(tree)
        ):
            continue
        module = importlib.import_module(
            ".".join(path.relative_to(_SRC.parent).with_suffix("").parts)
        )
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "dialog_buttons"
            ):
                continue
            labels: list[tuple[object, str]] = []
            for arg in node.args:
                if not (
                    isinstance(arg, ast.Attribute)
                    and isinstance(arg.value, ast.Name)
                    and hasattr(module, arg.value.id)
                ):
                    break
                labels.append((getattr(module, arg.value.id), arg.attr))
            else:
                if labels:
                    found.append((f"{path.name}:{node.lineno}", tuple(labels)))
    return found


_LABEL_SETS = _label_sets()


def test_the_rows_of_buttons_are_found() -> None:
    assert len(_LABEL_SETS) > 50


@pytest.fixture
def desktop() -> Iterator[None]:
    ctx = imgui.create_context()
    io = imgui.get_io()
    io.display_size = imgui.ImVec2(1280, 800)
    io.backend_flags |= imgui.BackendFlags_.renderer_has_textures
    yield
    imgui.destroy_context(ctx)
    strings.set_locale("en")


@pytest.mark.usefixtures("desktop")
@pytest.mark.parametrize("language", ["en", *TRANSLATED])
def test_every_row_of_buttons_fits_one_row(language: str) -> None:
    strings.set_locale(language)
    problems: list[str] = []
    for where, labels in _LABEL_SETS:
        texts = [getattr(owner, name) for owner, name in labels]
        rects, left, right = _rects(
            *texts, body=lambda: imgui.text("?"), title=f"{where}###dialog"
        )
        if not (_one_row(rects) and _inside(rects, left, right)):
            problems.append(f"{where}: {texts}")
    assert not problems, "\n".join(problems)


@pytest.mark.usefixtures("context")
@pytest.mark.parametrize("language", ["en", *TRANSLATED])
def test_read_from_device_shows_its_three_buttons_in_one_row(
    monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    from editor_gui.plugins.project.ui import read_device_dialog

    rects: list[_Rect] = []

    def recording(*labels: str, **_kwargs: object) -> int | None:
        rects.clear()
        return dialog_buttons(*labels, rects=rects)

    monkeypatch.setattr(read_device_dialog, "dialog_buttons", recording)
    strings.set_locale(language)
    try:
        dialog = read_device_dialog.ReadDeviceDialog(lambda _node, _group: None)
        dialog.open(1, "1.0.47 OpenKNX: RaumController PRODUKTIV")
        for _frame in range(4):
            imgui.new_frame()
            dialog.render()
            imgui.end_frame()
            imgui.render()
    finally:
        strings.set_locale("en")
    assert len(rects) == 3 and _one_row(rects)
