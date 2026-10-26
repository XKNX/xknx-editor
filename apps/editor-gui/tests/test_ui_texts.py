"""Text the UI shows comes from a strings module (translated), never a fixed string in the UI code.

Scans every call that puts its first argument on screen (``imgui.text``, ``button``,
``set_tooltip``, ``menu_item``, ``checkbox``, dialog titles, …) for a string or f-string literal
with words in its visible part (before ``##``). Technical texts that stay as they are in every
language are listed in ``TECHNICAL`` with the reason.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1] / "src" / "editor_gui"

# imgui calls that show their first argument.
_IMGUI_SHOWN = {
    "begin",
    "begin_combo",
    "begin_menu",
    "begin_popup_modal",
    "begin_tab_item",
    "bullet_text",
    "button",
    "checkbox",
    "collapsing_header",
    "combo",
    "drag_float",
    "drag_int",
    "input_float",
    "input_int",
    "input_text",
    "input_text_with_hint",
    "label_text",
    "menu_item",
    "progress_bar",
    "radio_button",
    "selectable",
    "separator_text",
    "set_tooltip",
    "slider_float",
    "slider_int",
    "small_button",
    "text",
    "text_colored",
    "text_disabled",
    "text_unformatted",
    "text_wrapped",
    "tree_node",
    "tree_node_ex",
}
# The app's own helpers that show their first argument.
_HELPERS_SHOWN = {
    "begin_combo",
    "begin_dialog",
    "checkbox",
    "dialog_buttons",
    "filter_box",
    "flash_toast",
    "_flash_toast",
    "_warn_toast",
    "tell_refused",
    "heading",
    "radio_button",
    "wrapped_tooltip",
}
_WORD = re.compile(r"[A-Za-z]{3,}")

# Text that is the same in every language, with the reason.
TECHNICAL = {
    "http://{}:{}/mcp": "the MCP server's address",
    "DPST-1-1": "example of a datapoint type token",
}


def _visible(node: ast.AST) -> str | None:
    """The part of a string literal the UI shows, when it has words."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        text = node.value
    elif isinstance(node, ast.JoinedStr):
        text = "".join(
            value.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
            else "{}"
            for value in node.values
        )
    else:
        return None
    visible = text.split("##")[0]
    return visible if _WORD.search(visible) else None


def _raw_texts() -> list[str]:
    found: list[str] = []
    for path in sorted(_ROOT.rglob("*.py")):
        if (
            "tests" in path.parts
            or path.name == "strings.py"
            or path.parent.name == "strings"
        ):
            continue
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                name = func.attr
                imgui = isinstance(func.value, ast.Name) and func.value.id == "imgui"
            elif isinstance(func, ast.Name):
                name, imgui = func.id, False
            else:
                continue
            if not ((imgui and name in _IMGUI_SHOWN) or name in _HELPERS_SHOWN):
                continue
            for arg in node.args[:2]:
                text = _visible(arg)
                if text is not None and text not in TECHNICAL:
                    where = path.relative_to(_ROOT).as_posix()
                    found.append(f"{where}:{node.lineno} {name}({text!r})")
                    break
    return found


def test_ui_texts_are_translated() -> None:
    raw = _raw_texts()
    assert not raw, (
        "UI text written into the code; put it in the strings module (translated), or list a "
        "technical text in TECHNICAL:\n" + "\n".join(raw)
    )


def test_technical_texts_are_still_used() -> None:
    """An entry of TECHNICAL that the code no longer shows is removed from the list."""
    shown: set[str] = set()
    for path in sorted(_ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            text = _visible(node)
            if text is not None:
                shown.add(text)
    assert set(TECHNICAL) <= shown, set(TECHNICAL) - shown


def test_every_panel_title_follows_the_language() -> None:
    """A language switch renames the panel tabs; each panel needs its translated title for that."""
    from editor_gui.main import _DOCK_LABELS, _DOCK_TAB_ORDER

    assert set(_DOCK_TAB_ORDER) <= set(_DOCK_LABELS), set(_DOCK_TAB_ORDER) - set(
        _DOCK_LABELS
    )


def test_the_language_menu_is_alphabetical_as_it_reads() -> None:
    from editor_gui.main import _language_items
    from editor_gui.strings import get_locale, set_locale

    previous = get_locale()
    try:
        set_locale("de")
        labels = [label for _code, label in _language_items()]
        assert labels[:4] == [
            "Chinesisch, vereinfacht (中文(中国))",
            "Dänisch (dansk)",
            "Deutsch",
            "Englisch (English)",
        ]
        assert labels[-2:] == ["Türkisch (Türkçe)", "Ukrainisch (українська)"]
        set_locale("es")
        labels = [label for _code, label in _language_items()]
        # Sorted without accents and case: "Español" right after "Danés", before "Finés".
        position = labels.index("Español (español)")
        assert labels[position - 1 : position + 2] == [
            "Danés (dansk)",
            "Español (español)",
            "Finés (suomi)",
        ]
    finally:
        set_locale(previous)
