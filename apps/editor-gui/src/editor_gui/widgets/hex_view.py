from __future__ import annotations

import struct

from imgui_bundle import imgui

from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.combo import begin_combo, combo
from editor_gui.widgets.dpi import px
from editor_gui.widgets.fonts import monospace, monospace_font
from editor_gui.widgets.strings import S

# Layout of the dump at 100 % scaling: rows 18 px apart, bytes 22 px apart and 6 px more between
# the two halves of a row, 22 px from the end of the address to the first byte, the characters 9 px
# further than a byte after the last one would be.
_ROW_GAP = 4.0
_BYTE_PITCH = 22.0
_HALF_GAP = 6.0
_ADDRESS_GAP = 22.0
_TEXT_GAP = 9.0


def _character(byte: int) -> str:
    """``byte`` as a character of the text beside the bytes: printable ASCII as itself, anything
    else as a dot."""
    return chr(byte) if 32 <= byte < 127 else "."


class HexView:
    """Hex viewer with selection, goto, param overlay, and reference diff."""

    def __init__(self) -> None:
        self.cols: int = 16
        self.grey_zeros: bool = True
        self.uppercase: bool = True
        self.show_ascii: bool = True
        self._hover_addr: int | None = None
        self._sel_anchor: int | None = None
        self._sel_cursor: int | None = None
        self._goto_buf: str = ""
        self._goto_param_buf: str = ""
        self._scroll_to: float | None = None
        self._child_h: float = 300.0
        # Height the rows below the hex area took in the last frame (see draw).
        self._footer_h: float | None = None

    def draw(
        self,
        data: bytes,
        base_addr: int = 0,
        byte_map: dict[int, tuple[str, str]] | None = None,
        ref: bytes | None = None,
    ) -> None:
        n = len(data)
        if n == 0:
            imgui.text_disabled(S.HEX_EMPTY)
            return

        line_h = imgui.get_text_line_height()
        row_gap = px(_ROW_GAP)
        row_h = line_h + row_gap
        glyph_w = imgui.calc_text_size("F").x + 1.0
        # Addresses, bytes and characters are in the font of fixed width; the characters in its
        # columns.
        with monospace():
            char_w = imgui.calc_text_size("F").x
        addr_digits = max(4, len(f"{base_addr + n - 1:X}"))
        pitch = px(_BYTE_PITCH)
        n_mid = self.cols // 2

        def hex_x(col: int) -> float:
            x = char_w * addr_digits + px(_ADDRESS_GAP) + col * pitch
            if col >= n_mid:
                x += px(_HALF_GAP)
            return x

        # A byte's highlight fills the room between it and its neighbours.
        room = (pitch - 2 * char_w) / 2
        ascii_x0 = hex_x(self.cols) + px(_TEXT_GAP) if self.show_ascii else 0.0
        n_rows = (n + self.cols - 1) // self.cols

        # Diff stats
        n_diffs = 0
        if ref is not None:
            cmp_len = min(n, len(ref))
            n_diffs = sum(1 for i in range(cmp_len) if data[i] != ref[i])
            n_diffs += abs(n - len(ref))

        sel_lo: int | None = None
        sel_hi: int | None = None
        if self._sel_anchor is not None and self._sel_cursor is not None:
            sel_lo = min(self._sel_anchor, self._sel_cursor)
            sel_hi = max(self._sel_anchor, self._sel_cursor)

        n_sel = (
            (sel_hi - sel_lo + 1) if (sel_lo is not None and sel_hi is not None) else 0
        )

        def _copy_selection() -> None:
            if sel_lo is None or sel_hi is None:
                return
            fmt_byte = "{:02X}" if self.uppercase else "{:02x}"
            imgui.set_clipboard_text(
                " ".join(fmt_byte.format(b) for b in data[sel_lo : sel_hi + 1])
            )

        # Reserve the height of the rows below (options, preview and parameter rows): what they
        # took in the last frame, so the window itself never needs a scrollbar; estimated in the
        # first one.
        sp = imgui.get_style().item_spacing.y
        preview_rows = 9 if ref is not None else 8
        footer_h = self._footer_h or (
            imgui.get_frame_height_with_spacing() * 2
            + sp
            + imgui.get_text_line_height_with_spacing() * preview_rows
            + sp * 2
        )

        imgui.begin_child(
            "##hx",
            imgui.ImVec2(-1.0, -footer_h),
            imgui.ChildFlags_.none,
            imgui.WindowFlags_.no_move | imgui.WindowFlags_.no_nav,
        )
        self._child_h = imgui.get_content_region_avail().y

        if self._scroll_to is not None:
            imgui.set_scroll_y(self._scroll_to)
            self._scroll_to = None

        io = imgui.get_io()
        mouse = io.mouse_pos
        self._hover_addr = None

        fmt_b = "{:02X}" if self.uppercase else "{:02x}"
        fmt_a = f"{{:0{addr_digits}X}}" if self.uppercase else f"{{:0{addr_digits}x}}"

        col_normal = imgui.get_color_u32(imgui.Col_.text)
        col_dim = imgui.get_color_u32(imgui.Col_.text_disabled)
        col_sel = imgui.get_color_u32(imgui.Col_.text_selected_bg)
        # The byte under the mouse as a row of a tree under it.
        col_hover = imgui.get_color_u32(imgui.Col_.header_hovered)
        col_diff = imgui.get_color_u32(imgui.ImVec4(0.9, 0.25, 0.1, 0.55))
        col_param = imgui.get_color_u32(imgui.ImVec4(0.85, 0.6, 0.1, 0.5))
        highlighted_param = self._goto_param_buf if self._goto_param_buf else None
        draw_list = imgui.get_window_draw_list()

        imgui.push_style_var(imgui.StyleVar_.item_spacing, imgui.ImVec2(0.0, row_gap))
        imgui.push_style_var(imgui.StyleVar_.frame_padding, imgui.ImVec2(0.0, 0.0))
        imgui.push_font(monospace_font(), imgui.get_style().font_size_base)

        clipper = imgui.ListClipper()
        clipper.begin(n_rows, row_h)
        while clipper.step():
            for row in range(clipper.display_start, clipper.display_end):
                start = row * self.cols
                chunk = data[start : start + self.cols]
                pos = imgui.get_cursor_screen_pos()
                row_x, row_y = pos.x, pos.y
                # The row's band: its text with half the gap to the next row above and below.
                top, bottom = row_y - row_gap / 2, row_y + line_h + row_gap / 2

                imgui.text_disabled(fmt_a.format(base_addr + start))

                for i, b in enumerate(chunk):
                    byte_idx = start + i
                    bx = row_x + hex_x(i)
                    p_min = imgui.ImVec2(bx - room, top)
                    p_max = imgui.ImVec2(bx + 2 * char_w + room, bottom)
                    # The byte's character in the text on the right.
                    cx = row_x + ascii_x0 + i * char_w
                    c_min = imgui.ImVec2(cx, top)
                    c_max = imgui.ImVec2(cx + char_w, bottom)
                    in_row = top <= mouse.y < bottom
                    is_hov = in_row and (
                        p_min.x <= mouse.x < p_max.x
                        or (self.show_ascii and cx <= mouse.x < c_max.x)
                    )
                    in_sel = (
                        sel_lo is not None
                        and sel_hi is not None
                        and sel_lo <= byte_idx <= sel_hi
                    )
                    is_diff = ref is not None and (
                        byte_idx >= len(ref) or ref[byte_idx] != b
                    )
                    is_param = (
                        highlighted_param is not None
                        and byte_map is not None
                        and byte_map.get(byte_idx, (None,))[0] == highlighted_param
                    )

                    if is_hov:
                        self._hover_addr = byte_idx
                        if imgui.is_mouse_clicked(imgui.MouseButton_.left):
                            if io.key_shift and self._sel_anchor is not None:
                                self._sel_cursor = byte_idx
                            else:
                                self._sel_anchor = byte_idx
                                self._sel_cursor = byte_idx
                        elif (
                            imgui.is_mouse_down(imgui.MouseButton_.left)
                            and self._sel_anchor is not None
                        ):
                            self._sel_cursor = byte_idx

                    background: int | None = None
                    if in_sel:
                        background = col_sel
                    elif is_diff:
                        background = col_diff
                    elif is_param:
                        background = col_param
                    elif is_hov:
                        background = col_hover
                    if background is not None:
                        draw_list.add_rect_filled(p_min, p_max, background)
                        if self.show_ascii:
                            draw_list.add_rect_filled(c_min, c_max, background)

                    col = (
                        col_dim
                        if (b == 0 and self.grey_zeros and not in_sel and not is_diff)
                        else col_normal
                    )
                    draw_list.add_text(imgui.ImVec2(bx, row_y), col, fmt_b.format(b))
                    if self.show_ascii:
                        draw_list.add_text(
                            imgui.ImVec2(cx, row_y), col_dim, _character(b)
                        )

        imgui.pop_font()
        imgui.pop_style_var(2)
        imgui.end_child()
        footer_top = imgui.get_cursor_pos_y()

        if imgui.is_item_hovered():
            if io.key_ctrl and imgui.is_key_pressed(imgui.Key.a):  # type: ignore[attr-defined]
                self._sel_anchor = 0
                self._sel_cursor = n - 1
            if io.key_ctrl and n_sel > 0 and imgui.is_key_pressed(imgui.Key.c):  # type: ignore[attr-defined]
                _copy_selection()

        imgui.separator()
        if imgui.button(f"{S.HEX_OPTIONS}##hx_options"):
            imgui.open_popup("##hx_opts")

        if imgui.begin_popup("##hx_opts"):
            col_options = [8, 16, 32]
            col_idx = col_options.index(self.cols) if self.cols in col_options else 1
            changed, col_idx = combo(
                f"{S.HEX_COLUMNS}##hx_columns", col_idx, ["8", "16", "32"]
            )
            if changed:
                self.cols = col_options[col_idx]
            _, self.grey_zeros = checkbox(S.HEX_GREY_ZEROS, self.grey_zeros)
            _, self.uppercase = checkbox(S.HEX_UPPERCASE, self.uppercase)
            _, self.show_ascii = checkbox(S.HEX_SHOW_ASCII, self.show_ascii)
            imgui.end_popup()

        imgui.same_line()
        imgui.begin_disabled(n_sel == 0)
        copy_label = S.HEX_COPY_BYTES.format(count=n_sel) if n_sel > 0 else S.HEX_COPY
        if imgui.button(f"{copy_label}###hx_copy"):
            _copy_selection()
        imgui.end_disabled()

        imgui.same_line()
        imgui.text_disabled("  " + S.HEX_GO_TO)
        imgui.same_line()
        imgui.set_next_item_width(glyph_w * (addr_digits + 2))
        entered, self._goto_buf = imgui.input_text(
            "##hx_goto",
            self._goto_buf,
            imgui.InputTextFlags_.chars_hexadecimal
            | imgui.InputTextFlags_.enter_returns_true,
        )
        if entered and self._goto_buf:
            try:
                rel = max(0, int(self._goto_buf, 16) - base_addr)
                row = rel // self.cols
                self._scroll_to = max(0.0, row * row_h - self._child_h * 0.5)
            except ValueError:
                pass

        imgui.same_line()
        rng = f"  {base_addr:0{addr_digits}X}..{base_addr + n - 1:0{addr_digits}X}"
        if not self.uppercase:
            rng = rng.lower()
        imgui.text_disabled(rng)

        if ref is not None:
            imgui.same_line()
            if n_diffs:
                imgui.text_colored(
                    imgui.ImVec4(0.9, 0.4, 0.2, 1.0),
                    "  " + S.HEX_DIFFS.format(count=n_diffs),
                )
            else:
                imgui.text_disabled("  " + S.HEX_NO_DIFFS)

        imgui.begin_disabled(byte_map is None)
        imgui.text_disabled(S.HEX_PARAMETER)
        imgui.same_line()
        imgui.set_next_item_width(-1.0)
        preview = self._goto_param_buf or S.HEX_PARAMETER_SEARCH
        if begin_combo("##hx_goto_param", preview):
            if imgui.is_window_appearing():
                imgui.set_keyboard_focus_here()
            _, self._goto_param_buf = imgui.input_text(
                "##hx_param_filter", self._goto_param_buf
            )
            if byte_map is not None:
                needle = self._goto_param_buf.lower()
                seen: set[str] = set()
                for byte_off, (pid, _) in sorted(byte_map.items()):
                    if pid in seen:
                        continue
                    if needle and needle not in pid.lower():
                        continue
                    seen.add(pid)
                    if imgui.selectable(pid, False)[0]:
                        self._goto_param_buf = pid
                        row = byte_off // self.cols
                        self._scroll_to = max(0.0, row * row_h - self._child_h * 0.5)
                        imgui.close_current_popup()
            imgui.end_combo()
        imgui.end_disabled()

        imgui.separator()
        addr = self._hover_addr
        label_w = glyph_w * 16.0

        def _row(label: str, value: str | None) -> None:
            imgui.text_disabled(label)
            imgui.same_line(label_w)
            if value is not None:
                imgui.text(value)
            else:
                imgui.text_disabled("---")

        if addr is not None and addr < n:
            abs_addr = base_addr + addr
            addr_fmt = (
                f"{{:0{addr_digits}X}}" if self.uppercase else f"{{:0{addr_digits}x}}"
            )
            _row("Offset:", addr_fmt.format(abs_addr))
            rem = n - addr
            b0 = data[addr]
            i8 = struct.unpack("b", bytes([b0]))[0]
            _row("Uint8 / Int8:", f"{b0} / {i8}  (0x{b0:02X})")
            _row("Binary:", f"{b0 >> 4:04b} {b0 & 0xF:04b}")
            if rem >= 2:
                (u16,) = struct.unpack_from(">H", data, addr)
                (s16,) = struct.unpack_from(">h", data, addr)
                _row("Uint16 / Int16:", f"{u16} / {s16}  (0x{u16:04X})")
            else:
                _row("Uint16 / Int16:", None)
            if rem >= 4:
                (u32,) = struct.unpack_from(">I", data, addr)
                (f32,) = struct.unpack_from(">f", data, addr)
                _row("Uint32:", f"{u32}  (0x{u32:08X})")
                _row("Float32:", f"{f32:.6g}")
            else:
                _row("Uint32:", None)
                _row("Float32:", None)
            if ref is not None:
                if addr < len(ref):
                    rb = ref[addr]
                    marker = "  !=" if rb != b0 else "  =="
                    _row("Ref byte:", f"{rb}  (0x{rb:02X}){marker}")
                else:
                    _row("Ref byte:", "(beyond ref)")
        else:
            _row("Offset:", None)
            _row("Uint8 / Int8:", None)
            _row("Binary:", None)
            _row("Uint16 / Int16:", None)
            _row("Uint32:", None)
            _row("Float32:", None)
            if ref is not None:
                _row("Ref byte:", None)

        imgui.separator()
        if byte_map is not None and addr is not None and addr < n:
            entry = byte_map.get(addr)
            if entry is not None:
                param_id, param_val = entry
                _row("Parameter:", param_id)
                _row("Value:", param_val)
            else:
                _row("Parameter:", None)
                _row("Value:", None)
        else:
            _row("Parameter:", None)
            _row("Value:", None)
        self._footer_h = imgui.get_cursor_pos_y() - footer_top
