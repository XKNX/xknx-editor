"""The GUI draws checkboxes, radio buttons, drop-downs, buttons, table headers and section headers
with the app's own widgets, never imgui's directly: those carry the app's look (size, colors of
the theme, header lines, sort arrow, arrow margin), which a raw imgui call would silently miss."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src" / "editor_gui"

# imgui function -> the app's widget to use instead, and the module that implements it (the only
# place allowed to call imgui's).
RULES: dict[str, tuple[str, str]] = {
    "checkbox": ("editor_gui.widgets.checkbox.checkbox", "widgets/checkbox.py"),
    "radio_button": ("editor_gui.widgets.checkbox.radio_button", "widgets/checkbox.py"),
    "combo": ("editor_gui.widgets.combo.combo", "widgets/combo.py"),
    "begin_combo": ("editor_gui.widgets.combo.begin_combo", "widgets/combo.py"),
    "end_combo": ("editor_gui.widgets.combo.end_combo", "widgets/combo.py"),
    "small_button": ("imgui.button (small buttons cut off descenders)", ""),
    "table_headers_row": ("editor_gui.widgets.tables.headers_row", "widgets/tables.py"),
    "collapsing_header": (
        "editor_gui.widgets.tree.collapsing_header (its arrow margin)",
        "widgets/tree.py",
    ),
}


def _sources() -> list[Path]:
    return sorted(
        path
        for path in SOURCE.rglob("*.py")
        if "tests" not in path.relative_to(SOURCE).parts
    )


def _raw_calls(path: Path) -> list[tuple[int, str]]:
    """The calls ``imgui.<name>(...)`` of a ruled imgui function in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    calls: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "imgui"
            and node.func.attr in RULES
        ):
            calls.append((node.lineno, node.func.attr))
    return calls


def test_the_sources_are_found() -> None:
    assert len(_sources()) > 100


@pytest.mark.parametrize("name", sorted(RULES))
def test_the_app_widget_is_used_instead_of_imgui(name: str) -> None:
    widget, home = RULES[name]
    found = [
        f"{path.relative_to(SOURCE).as_posix()}:{line}"
        for path in _sources()
        for line, called in _raw_calls(path)
        if called == name and path.relative_to(SOURCE).as_posix() != home
    ]
    assert not found, f"imgui.{name} used instead of {widget}: {', '.join(found)}"


def test_a_raw_call_is_found(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from imgui_bundle import imgui\n"
        "# imgui.checkbox( in a comment is no call\n"
        "changed, value = imgui.checkbox(\n"
        "    'Label', True\n"
        ")\n",
        encoding="utf-8",
    )
    assert _raw_calls(sample) == [(3, "checkbox")]
