"""A text field with bold, italic and underlined text, for comments and installation hints.

The field shows the text formatted while it is edited: the text wraps at words, the caret and the
selection work with mouse and keyboard as in any text field (Ctrl+B/I/U switch the formatting too),
and three buttons below it make the selection bold, italic or underlined. Typed text, IME input and
a touch screen's keyboard reach it like an ImGui text field. An edit is handed back once the field
loses the focus, as one change.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass

from imgui_bundle import imgui

from editor_gui.widgets.dpi import px
from editor_gui.widgets.fonts import styled_font
from editor_gui.widgets.rich_document import (
    BOLD,
    ITALIC,
    UNDERLINE,
    Pos,
    RichDocument,
)
from editor_gui.widgets.strings import S
from xknxeditor.proj.core.rich_text import (
    RichText,
    parse_rich_text,
    rich_text_rtf_like,
)

# What was copied last from a rich text field: pasted again (same clipboard text) it keeps its
# formatting.
_copied: tuple[str, RichText] | None = None
# The text selected in the rich text field being edited, for the web page's copy (see web).
_selection = ""
_TAB_SPACES = 4


def active_selection() -> str:
    """The text selected in the rich text field being edited ("" without one)."""
    return _selection


@dataclass
class _Row:
    """One wrapped line on screen: characters ``start`` to ``end`` of paragraph ``line``; ``xs``
    holds the x of each character boundary from ``start`` to ``end``."""

    line: int
    start: int
    end: int
    xs: list[float]


class RichTextEdit:
    """One rich text field. ``render`` shows the stored text of an element (``key``) and returns
    ``(key, rtf)`` once an edit of it ends; ``tick`` (every frame) ends an edit whose field is no
    longer drawn."""

    def __init__(self, ident: str) -> None:
        self._id = ident
        self._key: Hashable = None
        self._source = ""
        self._doc = RichDocument()
        self._focused = False
        self._dirty = False
        self._last_frame = -1
        self._version = 0
        self._rows: list[_Row] = []
        self._layout_key: tuple[object, ...] | None = None
        self._widths: dict[tuple[int, float, str], float] = {}
        self._line_height = 0.0
        self._origin = imgui.ImVec2(0.0, 0.0)
        self._blink_start = 0.0
        self._preferred_x: float | None = None
        self._scroll_to_caret = False
        self._button_id = 0

    # --- the stored text -------------------------------------------------------------------

    def _load(self, key: Hashable, stored: str) -> None:
        self._key = key
        self._source = stored
        self._doc = RichDocument.from_rich_text(parse_rich_text(stored))
        self._dirty = False
        self._focused = False
        self._changed()

    def _commit(self) -> tuple[Hashable, str] | None:
        if not self._dirty:
            return None
        self._dirty = False
        text = self._doc.rich_text()
        if text == parse_rich_text(self._source):
            return None
        rtf = rich_text_rtf_like(text, self._source)
        self._source = rtf
        return self._key, rtf

    def tick(self) -> tuple[Hashable, str] | None:
        """End the edit of a field that was not drawn in the last frame (its tab was left)."""
        if self._focused and self._last_frame < imgui.get_frame_count() - 1:
            self._unfocus()
            return self._commit()
        return None

    def _unfocus(self) -> None:
        global _selection
        self._focused = False
        _selection = ""

    def _changed(self) -> None:
        self._version += 1
        self._scroll_to_caret = True
        self._blink_start = imgui.get_time() if imgui.get_current_context() else 0.0

    # --- drawing ---------------------------------------------------------------------------

    def render(
        self, key: Hashable, stored: str, height: float
    ) -> tuple[Hashable, str] | None:
        """The field for ``stored`` (RTF) of element ``key``, ``height`` high (the formatting
        buttons below it come on top)."""
        commit = None
        if key != self._key:
            commit = self._commit()
            self._load(key, stored)
        elif not self._focused and stored != self._source:
            self._load(key, stored)
        self._last_frame = imgui.get_frame_count()

        imgui.begin_group()
        border = self._focused
        if border:
            imgui.push_style_color(
                imgui.Col_.border, imgui.get_style_color_vec4(imgui.Col_.nav_cursor)
            )
            imgui.push_style_var(imgui.StyleVar_.child_border_size, max(px(1.0), 1.0))
        flags = imgui.ChildFlags_.frame_style | (
            imgui.ChildFlags_.borders if border else 0
        )
        if imgui.begin_child(f"##{self._id}", imgui.ImVec2(0.0, height), flags):
            self._text_area()
        imgui.end_child()
        if border:
            imgui.pop_style_var()
            imgui.pop_style_color()
        self._buttons()
        imgui.end_group()

        inside = imgui.is_item_hovered(
            imgui.HoveredFlags_.allow_when_blocked_by_active_item
        )
        clicked = imgui.is_mouse_clicked(0) or imgui.is_mouse_clicked(1)
        if self._focused and ((clicked and not inside) or self._other_field_active()):
            self._unfocus()
            commit = commit or self._commit()
        if self._focused:
            context = imgui.get_current_context()
            context.want_text_input_next_frame = 1
        return commit

    def _other_field_active(self) -> bool:
        active = imgui.internal.get_active_id()
        if active in (0, self._button_id):
            return False
        return active == imgui.get_current_context().input_text_state.id_

    def _text_area(self) -> None:
        style = imgui.get_style()
        font = imgui.get_font()
        size = imgui.get_font_size()
        self._line_height = imgui.get_text_line_height() + px(3.0)
        width = max(imgui.get_content_region_avail().x, px(20.0))
        self._layout(font, size, width)
        self._origin = imgui.get_cursor_screen_pos()
        area_height = max(
            len(self._rows) * self._line_height,
            imgui.get_content_region_avail().y,
            self._line_height,
        )
        imgui.invisible_button(f"##{self._id}_text", imgui.ImVec2(width, area_height))
        self._button_id = imgui.get_item_id()
        if imgui.is_item_hovered():
            imgui.set_mouse_cursor(imgui.MouseCursor_.text_input)
        self._mouse()
        if self._focused:
            self._keyboard()
        self._layout(font, size, width)
        self._draw(font, size)
        if self._focused and self._scroll_to_caret:
            self._scroll_to_caret = False
            row = self._row_of(self._doc.caret)
            top = row * self._line_height
            view = imgui.get_window_height() - 2.0 * style.frame_padding.y
            scroll = imgui.get_scroll_y()
            if top < scroll:
                imgui.set_scroll_y(top)
            elif top + self._line_height > scroll + view:
                imgui.set_scroll_y(top + self._line_height - view)

    def _char_width(self, font: imgui.ImFont, size: float, fmt: int, ch: str) -> float:
        key = (fmt & (BOLD | ITALIC), size, ch)
        width = self._widths.get(key)
        if width is None:
            face = styled_font(bool(fmt & BOLD), bool(fmt & ITALIC)) or font
            baked = face.get_font_baked(size)
            if ch == "\t":
                width = baked.get_char_advance(ord(" ")) * _TAB_SPACES
            else:
                width = baked.get_char_advance(ord(ch))
            self._widths[key] = width
        return width

    def _layout(self, font: imgui.ImFont, size: float, width: float) -> None:
        key = (self._version, width, size)
        if key == self._layout_key:
            return
        self._layout_key = key
        rows: list[_Row] = []
        for index, (text, formats) in enumerate(
            zip(self._doc.lines, self._doc.formats, strict=True)
        ):
            widths = [
                self._char_width(font, size, fmt, ch)
                for ch, fmt in zip(text, formats, strict=True)
            ]
            start = 0
            while True:
                x = 0.0
                i = start
                breakable = -1
                while i < len(text):
                    if x + widths[i] > width and i > start and text[i] != " ":
                        break
                    x += widths[i]
                    if text[i] in " \t":
                        breakable = i + 1
                    i += 1
                end = (
                    len(text)
                    if i >= len(text)
                    else breakable
                    if breakable > start
                    else i
                )
                xs = [0.0]
                for k in range(start, end):
                    xs.append(xs[-1] + widths[k])
                rows.append(_Row(index, start, end, xs))
                if end >= len(text):
                    break
                start = end
        self._rows = rows

    def _row_of(self, pos: Pos) -> int:
        """The row showing ``pos``; at a wrap the caret goes to the start of the next row."""
        line, col = pos
        for index, row in enumerate(self._rows):
            if row.line == line and row.start <= col <= row.end:
                following = index + 1 < len(self._rows) and (
                    self._rows[index + 1].line == line
                    and self._rows[index + 1].start == col
                )
                if not following:
                    return index
        return len(self._rows) - 1

    def _caret_xy(self, pos: Pos) -> tuple[float, float]:
        index = self._row_of(pos)
        row = self._rows[index]
        return row.xs[pos[1] - row.start], index * self._line_height

    def _hit(self, x: float, y: float) -> Pos:
        """The text position nearest to the screen point ``(x, y)``."""
        index = int((y - self._origin.y) // self._line_height)
        index = max(0, min(index, len(self._rows) - 1))
        row = self._rows[index]
        return (row.line, self._column_at(row, index, x - self._origin.x))

    def _column_at(self, row: _Row, index: int, x: float) -> int:
        col = row.start
        for k in range(row.end - row.start):
            if x < (row.xs[k] + row.xs[k + 1]) / 2.0:
                break
            col = row.start + k + 1
        wrapped = index + 1 < len(self._rows) and self._rows[index + 1].line == row.line
        if wrapped and col == row.end and row.end > row.start:
            col -= 1  # stay on this row, before the space it wrapped at
        return col

    def _draw(self, font: imgui.ImFont, size: float) -> None:
        draw = imgui.get_window_draw_list()
        text_color = imgui.get_color_u32(imgui.Col_.text)
        selection_color = imgui.get_color_u32(imgui.Col_.text_selected_bg)
        ox, oy = self._origin.x, self._origin.y
        line_height = self._line_height
        text_offset = (line_height - imgui.get_text_line_height()) / 2.0
        ascent = font.get_font_baked(size).ascent
        doc = self._doc
        start, end = doc.selection()
        selected = doc.has_selection() and self._focused
        clip_top = imgui.get_scroll_y() - line_height
        clip_bottom = clip_top + imgui.get_window_height() + 2.0 * line_height
        for index, row in enumerate(self._rows):
            top = index * line_height
            if top < clip_top or top > clip_bottom:
                continue
            y = oy + top
            if selected:
                a = max(start, (row.line, row.start))
                b = min(end, (row.line, row.end))
                last_of_paragraph = (
                    index + 1 == len(self._rows)
                    or self._rows[index + 1].line != row.line
                )
                # The line break at a paragraph's end counts as selected too.
                breaks = last_of_paragraph and start <= b < end
                if a <= b and (a < b or breaks):
                    x0 = ox + row.xs[a[1] - row.start]
                    x1 = ox + row.xs[b[1] - row.start]
                    if breaks:
                        x1 += px(4.0)
                    if x1 > x0:
                        draw.add_rect_filled(
                            imgui.ImVec2(x0, y),
                            imgui.ImVec2(x1, y + line_height),
                            selection_color,
                        )
            text = doc.lines[row.line]
            formats = doc.formats[row.line]
            k = row.start
            while k < row.end:
                fmt = formats[k]
                stop = k + 1
                while stop < row.end and formats[stop] == fmt:
                    stop += 1
                piece = text[k:stop].replace("\t", " " * _TAB_SPACES)
                x = ox + row.xs[k - row.start]
                face = styled_font(bool(fmt & BOLD), bool(fmt & ITALIC)) or font
                draw.add_text(
                    face, size, imgui.ImVec2(x, y + text_offset), text_color, piece
                )
                if fmt & UNDERLINE:
                    x_end = ox + row.xs[stop - row.start]
                    under = y + text_offset + ascent + px(1.5)
                    draw.add_line(
                        imgui.ImVec2(x, under),
                        imgui.ImVec2(x_end, under),
                        text_color,
                        max(px(1.0), 1.0),
                    )
                k = stop
        if self._focused:
            x, top = self._caret_xy(doc.caret)
            caret_top = imgui.ImVec2(ox + x, oy + top + px(1.0))
            caret_bottom = imgui.ImVec2(ox + x, oy + top + line_height - px(1.0))
            blink = imgui.get_time() - self._blink_start
            if not imgui.get_io().config_input_text_cursor_blink or blink % 1.2 < 0.8:
                draw.add_line(caret_top, caret_bottom, text_color, max(px(1.0), 1.0))
            ime = imgui.get_current_context().platform_ime_data
            ime.want_visible = True
            ime.want_text_input = True
            ime.input_pos = caret_top
            ime.input_line_height = line_height
            ime.viewport_id = imgui.get_window_viewport().id_

    def _buttons(self) -> None:
        """Bold, italic and underline below the field, on its right."""
        style = imgui.get_style()
        size = imgui.get_frame_height()
        width = size * 1.25
        total = 3 * width + 2 * style.item_spacing.x
        imgui.set_cursor_pos_x(
            imgui.get_cursor_pos_x()
            + max(0.0, imgui.get_content_region_avail().x - total)
        )
        font = imgui.get_font()
        font_size = imgui.get_font_size()
        for index, (flag, tip, keys) in enumerate(
            (
                (BOLD, S.RICH_TEXT_BOLD, S.SHORTCUT_BOLD),
                (ITALIC, S.RICH_TEXT_ITALIC, S.SHORTCUT_ITALIC),
                (UNDERLINE, S.RICH_TEXT_UNDERLINE, S.SHORTCUT_UNDERLINE),
            )
        ):
            if index:
                imgui.same_line()
            on = self._doc.active(flag)
            if on:
                imgui.push_style_color(
                    imgui.Col_.button,
                    imgui.get_style_color_vec4(imgui.Col_.button_active),
                )
            if imgui.button(f"##{self._id}_format{flag}", imgui.ImVec2(width, size)):
                self._toggle(flag)
            if on:
                imgui.pop_style_color()
            if imgui.is_item_hovered():
                imgui.set_tooltip(f"{tip} ({keys})")
            face = styled_font(flag == BOLD, flag == ITALIC) or font
            low, high = imgui.get_item_rect_min(), imgui.get_item_rect_max()
            glyph = face.get_font_baked(font_size).get_char_advance(ord("a"))
            x = (low.x + high.x - glyph) / 2.0
            y = (low.y + high.y - imgui.get_text_line_height()) / 2.0
            color = imgui.get_color_u32(imgui.Col_.text)
            draw = imgui.get_window_draw_list()
            draw.add_text(face, font_size, imgui.ImVec2(x, y), color, "a")
            if flag == UNDERLINE:
                under = y + font.get_font_baked(font_size).ascent + px(1.5)
                draw.add_line(
                    imgui.ImVec2(x, under), imgui.ImVec2(x + glyph, under), color, 1.0
                )

    def _toggle(self, flag: int) -> None:
        had_selection = self._doc.has_selection()
        self._doc.toggle(flag)
        self._focused = True
        if had_selection:
            self._dirty = True
        self._changed()

    # --- input -----------------------------------------------------------------------------

    def _mouse(self) -> None:
        doc = self._doc
        io = imgui.get_io()
        mouse = imgui.get_mouse_pos()
        if imgui.is_item_clicked(0):
            self._focused = True
            pos = self._hit(mouse.x, mouse.y)
            if imgui.is_mouse_double_clicked(0):
                start, end = doc.word_at(pos)
                doc.move_to(start)
                doc.move_to(end, extend=True)
            else:
                doc.move_to(pos, extend=io.key_shift)
            self._preferred_x = None
            self._changed()
        elif (
            imgui.is_item_active()
            and imgui.is_mouse_dragging(0)
            and not imgui.is_mouse_double_clicked(0)
        ):
            pos = self._hit(mouse.x, mouse.y)
            if pos != doc.caret:
                doc.move_to(pos, extend=True)
                self._changed()
        elif imgui.is_item_clicked(1):
            self._focused = True
            pos = self._hit(mouse.x, mouse.y)
            start, end = doc.selection()
            if not (doc.has_selection() and start <= pos <= end):
                doc.move_to(pos)
            imgui.open_popup(f"##{self._id}_menu")
        if imgui.begin_popup(f"##{self._id}_menu"):
            if imgui.menu_item(
                S.RICH_TEXT_CUT, S.SHORTCUT_CUT, False, doc.has_selection()
            )[0]:
                self._cut()
            if imgui.menu_item(
                S.RICH_TEXT_COPY, S.SHORTCUT_COPY, False, doc.has_selection()
            )[0]:
                self._copy()
            if imgui.menu_item(S.RICH_TEXT_PASTE, S.SHORTCUT_PASTE, False, True)[0]:
                self._paste()
            imgui.end_popup()

    def _keyboard(self) -> None:
        global _selection
        io = imgui.get_io()
        doc = self._doc
        ctrl = io.key_ctrl or io.key_super
        shift = io.key_shift

        def pressed(key: imgui.Key) -> bool:
            return imgui.is_key_pressed(key, True)

        before = (doc.caret, doc.anchor, self._version)
        edited = False
        vertical = False
        if pressed(imgui.Key.left_arrow):
            if doc.has_selection() and not shift:
                doc.move_to(doc.selection()[0])
            else:
                doc.move_to(doc.left(doc.caret, word=ctrl), extend=shift)
        elif pressed(imgui.Key.right_arrow):
            if doc.has_selection() and not shift:
                doc.move_to(doc.selection()[1])
            else:
                doc.move_to(doc.right(doc.caret, word=ctrl), extend=shift)
        elif pressed(imgui.Key.up_arrow) or pressed(imgui.Key.down_arrow):
            self._vertical(-1 if imgui.is_key_down(imgui.Key.up_arrow) else 1, shift)
            vertical = True
        elif pressed(imgui.Key.page_up) or pressed(imgui.Key.page_down):
            rows = max(
                1, int(imgui.get_window_height() // max(self._line_height, 1.0)) - 1
            )
            down = imgui.is_key_down(imgui.Key.page_down)
            self._vertical(rows if down else -rows, shift)
            vertical = True
        elif pressed(imgui.Key.home):
            if ctrl:
                doc.move_to((0, 0), extend=shift)
            else:
                row = self._rows[self._row_of(doc.caret)]
                doc.move_to((row.line, row.start), extend=shift)
        elif pressed(imgui.Key.end):
            if ctrl:
                doc.move_to((len(doc.lines) - 1, len(doc.lines[-1])), extend=shift)
            else:
                index = self._row_of(doc.caret)
                row = self._rows[index]
                col = self._column_at(row, index, row.xs[-1] + 1.0)
                doc.move_to((row.line, col), extend=shift)
        elif pressed(imgui.Key.backspace):
            doc.backspace(word=ctrl)
            edited = True
        elif pressed(imgui.Key.delete):
            if shift and doc.has_selection():
                self._cut()
            else:
                doc.delete(word=ctrl)
            edited = True
        elif pressed(imgui.Key.enter) or pressed(imgui.Key.keypad_enter):
            doc.insert("\n")
            edited = True
        elif ctrl and pressed(imgui.Key.a):
            doc.select_all()
        elif ctrl and (pressed(imgui.Key.c) or pressed(imgui.Key.insert)):
            self._copy()
        elif ctrl and pressed(imgui.Key.x):
            self._cut()
        elif (ctrl and pressed(imgui.Key.v)) or (shift and pressed(imgui.Key.insert)):
            self._paste()
        elif ctrl and pressed(imgui.Key.z):
            edited = doc.redo() if shift else doc.undo()
        elif ctrl and pressed(imgui.Key.y):
            edited = doc.redo()
        elif ctrl and pressed(imgui.Key.b):
            self._toggle(BOLD)
        elif ctrl and pressed(imgui.Key.i):
            self._toggle(ITALIC)
        elif ctrl and pressed(imgui.Key.u):
            self._toggle(UNDERLINE)
        queue = io.input_queue_characters
        typed = "".join(chr(c) for c in queue if c >= 32 and c != 127)
        if typed and not (ctrl and not io.key_alt):
            doc.insert(typed)
            edited = True
        queue.clear()
        if edited:
            self._dirty = True
        if not vertical and (doc.caret, doc.anchor) != before[:2]:
            self._preferred_x = None
        if edited or (doc.caret, doc.anchor, self._version) != before:
            self._changed()
        _selection = doc.selected_text()

    def _vertical(self, rows: int, extend: bool) -> None:
        doc = self._doc
        index = self._row_of(doc.caret)
        if self._preferred_x is None:
            self._preferred_x = self._caret_xy(doc.caret)[0]
        target = max(0, min(index + rows, len(self._rows) - 1))
        if target == index:
            pos = (0, 0) if rows < 0 else (len(doc.lines) - 1, len(doc.lines[-1]))
        else:
            row = self._rows[target]
            pos = (row.line, self._column_at(row, target, self._preferred_x))
        preferred = self._preferred_x
        doc.move_to(pos, extend=extend)
        self._preferred_x = preferred

    def _copy(self) -> None:
        global _copied
        if not self._doc.has_selection():
            return
        text = self._doc.selected_text()
        _copied = (text, self._doc.selected_rich_text())
        imgui.set_clipboard_text(text)

    def _cut(self) -> None:
        if not self._doc.has_selection():
            return
        self._copy()
        self._doc.delete_selection()
        self._dirty = True
        self._changed()

    def _paste(self) -> None:
        text = (imgui.get_clipboard_text() or "").replace("\r\n", "\n")
        if not text:
            return
        if _copied is not None and _copied[0] == text:
            self._doc.insert_rich(_copied[1])
        else:
            self._doc.insert(text)
        self._dirty = True
        self._changed()
