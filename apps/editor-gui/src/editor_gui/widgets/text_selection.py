"""Selecting text that is drawn line by line (the help of a device's parameters, release notes),
as in a read-only text field: dragging the mouse selects from where it was pressed, a double
click a word, Shift with a click extends the selection, Ctrl+A (Cmd+A) selects everything, and
Ctrl+C (Cmd+C) or the context menu copies the selection.

The text keeps its own layout (fonts, sizes, list items): the widget only learns where each line
is drawn (:meth:`TextSelection.line`) and draws the selection behind it.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass, field

from imgui_bundle import imgui

from editor_gui.widgets import text_layout
from editor_gui.widgets.strings import S

# A place in the text: the line and the character in it.
_Pos = tuple[int, int]

# The text selected in the text shown last (with the frame it was shown in), for the web page's
# copy (see web): a browser lets a page copy only in the key press itself.
_shown: tuple[int, str] = (-1, "")


def active_selection() -> str:
    """The text selected in selectable text shown in this frame or the one before ("" without
    one)."""
    frame, text = _shown
    return text if frame >= imgui.get_frame_count() - 1 else ""


@dataclass
class _Line:
    text: str
    # What comes before the line when the text is copied: a space for a line a paragraph was
    # wrapped into, a line feed for the first line of a block.
    joiner: str
    x: float
    y: float
    height: float
    # What a copy of the line from its start begins with: the marker of a list item.
    prefix: str = ""
    # Where each character starts, from the line's left (and where the last one ends), and the
    # width of a space, in the line's font; measured only while the text is selected or under
    # the mouse.
    edges: list[float] = field(default_factory=list[float])
    space: float = 0.0

    @property
    def width(self) -> float:
        return self.edges[-1] if self.edges else 0.0


def _word_bounds(text: str, index: int) -> tuple[int, int]:
    """The word (or run of spaces, or single other character) around ``index``."""
    if not text:
        return 0, 0
    index = min(index, len(text) - 1)

    def kind(char: str) -> int:
        return 0 if char.isalnum() or char == "_" else 1 if char.isspace() else 2

    own = kind(text[index])
    if own == 2:
        return index, index + 1
    start = index
    while start > 0 and kind(text[start - 1]) == own:
        start -= 1
    end = index + 1
    while end < len(text) and kind(text[end]) == own:
        end += 1
    return start, end


class TextSelection:
    """The selection of one text shown in its own window (a child window of its own: the clicks
    in it select, its focus takes the keys). Between :meth:`begin` and :meth:`end` the text is
    drawn, each line announced with :meth:`line` right before it is drawn at the cursor."""

    def __init__(self, ident: str) -> None:
        self._ident = ident
        self._key: Hashable = None
        self._lines: list[_Line] = []
        self._anchor: _Pos | None = None
        self._focus: _Pos | None = None
        self._dragging = False
        self._draw_list: imgui.ImDrawList | None = None
        self._measure = False

    # -- the text ------------------------------------------------------------------------------
    def begin(self, key: Hashable) -> None:
        """Start drawing the text ``key`` names (another text drops the selection)."""
        if key != self._key:
            self._key = key
            self.clear()
        self._lines = []
        # Where the characters are is only needed to draw a selection or to follow the mouse.
        self._measure = (
            self._anchor is not None
            or self._dragging
            or imgui.is_window_hovered(imgui.HoveredFlags_.allow_when_blocked_by_popup)
        )
        # The selection behind the text: drawn into the lower channel once the lines are known.
        self._draw_list = imgui.get_window_draw_list()
        self._draw_list.channels_split(2)
        self._draw_list.channels_set_current(1)

    def line(self, text: str, joiner: str, prefix: str = "") -> None:
        """The line ``text`` is drawn at the cursor next, in the current font; ``joiner`` is
        what comes before it when it is copied (" " inside a paragraph, "\\n" before a block),
        ``prefix`` what a copy from its start begins with (a list item's marker)."""
        if self._draw_list is None:
            return
        origin = imgui.get_cursor_screen_pos()
        line = _Line(
            text,
            joiner if self._lines else "",
            origin.x,
            origin.y,
            imgui.get_text_line_height(),
            prefix,
        )
        if self._measure:
            line.edges = [
                text_layout.line_width(text, 0, n) for n in range(len(text) + 1)
            ]
            line.space = text_layout.text_width(" ")
        self._lines.append(line)

    def end(self) -> None:
        """Handle the mouse and the keys on the text drawn and draw the selection behind it."""
        global _shown
        draw_list = self._draw_list
        if draw_list is None:
            return
        self._draw_list = None
        self._mouse()
        self._keys()
        draw_list.channels_set_current(0)
        self._draw_selection(draw_list)
        draw_list.channels_merge()
        self._context_menu()
        selected = self.selected_text()
        if selected:
            _shown = (imgui.get_frame_count(), selected)

    # -- the selection -------------------------------------------------------------------------
    def clear(self) -> None:
        self._anchor = self._focus = None
        self._dragging = False

    def select_all(self) -> None:
        if self._lines:
            self._anchor = (0, 0)
            self._focus = (len(self._lines) - 1, len(self._lines[-1].text))

    def _range(self) -> tuple[_Pos, _Pos] | None:
        if self._anchor is None or self._focus is None or self._anchor == self._focus:
            return None
        start, stop = sorted((self._anchor, self._focus))
        if start[0] >= len(self._lines):
            return None
        last = min(stop[0], len(self._lines) - 1)
        return start, (last, min(stop[1], len(self._lines[last].text)))

    def selected_text(self) -> str:
        """The text selected, its wrapped lines joined as the text was before it was wrapped."""
        span = self._range()
        if span is None:
            return ""
        (first, start), (last, stop) = span
        parts: list[str] = []
        for number in range(first, last + 1):
            line = self._lines[number]
            begin = start if number == first else 0
            end = stop if number == last else len(line.text)
            if number != first:
                parts.append(line.joiner)
            if begin == 0 and end > 0:
                parts.append(line.prefix)
            parts.append(line.text[begin:end])
        return "".join(parts)

    # -- input ---------------------------------------------------------------------------------
    def _hit(self, x: float, y: float) -> _Pos | None:
        """The place in the text nearest to the point (x, y), None without lines."""
        if not self._lines:
            return None
        if y < self._lines[0].y:
            return (0, 0)
        number = len(self._lines) - 1
        for n, line in enumerate(self._lines):
            if y < line.y + line.height:
                number = n
                break
        line = self._lines[number]
        edges = line.edges
        if not edges:
            return None
        offset = x - line.x
        for n in range(len(line.text)):
            if offset < (edges[n] + edges[n + 1]) / 2:
                return (number, n)
        return (number, len(line.text))

    def _over_text(self, x: float, y: float) -> bool:
        return any(
            line.y <= y < line.y + line.height and line.x <= x <= line.x + line.width
            for line in self._lines
        )

    def _mouse(self) -> None:
        io = imgui.get_io()
        mouse = io.mouse_pos
        hovered = imgui.is_window_hovered()
        if hovered and self._over_text(mouse.x, mouse.y):
            imgui.set_mouse_cursor(imgui.MouseCursor_.text_input)
        if hovered and imgui.is_mouse_clicked(imgui.MouseButton_.left):
            hit = self._hit(mouse.x, mouse.y)
            if hit is None:
                self.clear()
            elif imgui.is_mouse_double_clicked(imgui.MouseButton_.left):
                start, end = _word_bounds(self._lines[hit[0]].text, hit[1])
                self._anchor, self._focus = (hit[0], start), (hit[0], end)
            else:
                if not (io.key_shift and self._anchor is not None):
                    self._anchor = hit
                self._focus = hit
                self._dragging = True
        if self._dragging:
            if imgui.is_mouse_down(imgui.MouseButton_.left):
                hit = self._hit(mouse.x, mouse.y)
                if hit is not None:
                    self._focus = hit
            else:
                self._dragging = False

    def _keys(self) -> None:
        if not imgui.is_window_focused():
            return
        if imgui.shortcut(int(imgui.Key.mod_ctrl) | int(imgui.Key.a)):
            self.select_all()
        if imgui.shortcut(int(imgui.Key.mod_ctrl) | int(imgui.Key.c)):
            self._copy()

    def _copy(self) -> None:
        text = self.selected_text()
        if text:
            imgui.set_clipboard_text(text)

    def _context_menu(self) -> None:
        popup = f"##{self._ident}_menu"
        if imgui.is_window_hovered() and imgui.is_mouse_released(
            imgui.MouseButton_.right
        ):
            imgui.open_popup(popup)
        if imgui.begin_popup(popup):
            if imgui.menu_item(
                S.COPY, S.SHORTCUT_COPY, False, bool(self.selected_text())
            )[0]:
                self._copy()
            imgui.end_popup()

    # -- drawing -------------------------------------------------------------------------------
    def _draw_selection(self, draw_list: imgui.ImDrawList) -> None:
        span = self._range()
        if span is None:
            return
        (first, start), (last, stop) = span
        color = imgui.get_color_u32(imgui.Col_.text_selected_bg)
        for number in range(first, last + 1):
            line = self._lines[number]
            edges = line.edges
            if not edges:
                continue
            begin = edges[start if number == first else 0]
            end = edges[stop if number == last else len(line.text)]
            if number != last:
                # The end of a line that goes on selected: room for what joins it to the next.
                end += line.space
            if end > begin:
                draw_list.add_rect_filled(
                    imgui.ImVec2(line.x + begin, line.y),
                    imgui.ImVec2(line.x + end, line.y + line.height),
                    color,
                )
