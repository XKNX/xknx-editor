import math
from collections.abc import Callable
from enum import Enum

from imgui_bundle import imgui

from editor_gui.color import color_u32
from editor_gui.net import TelegramSource
from editor_gui.plugins.network.records import CemiRecord, TelegramRecord
from editor_gui.plugins.network.strings import S
from editor_gui.widgets.dpi import px, px_vec2


class CaptureState(Enum):
    STOPPED = "stopped"
    CAPTURING = "capturing"


SERVICE_COLORS = {
    "GroupValueWrite": (0.4, 0.7, 0.4),
    "GroupValueRead": (0.5, 0.6, 0.8),
    "GroupValueResponse": (0.7, 0.6, 0.4),
}
TPCI_ABBREV = {
    "TDataGroup": "Group",
    "TDataBroadcast": "Bcast",
    "TDataIndividual": "Indiv",
    "TConnect": "Conn",
    "TDisconnect": "Disc",
    "TAck": "Ack",
    "TNak": "Nak",
}
DEFAULT_SERVICE_COLOR = (0.5, 0.5, 0.5)
SOURCE_COLORS = {
    TelegramSource.CONNECTION: (0.9, 0.7, 0.3),  # amber
    TelegramSource.PROXY: (0.7, 0.5, 0.9),  # violet
    TelegramSource.VIRTUAL: (0.3, 0.75, 0.8),  # teal
}


class NetworkPanel:
    def __init__(
        self,
        get_telegrams: Callable[[], list[TelegramRecord]],
        get_cemi_records: Callable[[], list[CemiRecord]],
        get_capture_state: Callable[[], CaptureState],
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_clear: Callable[[], None],
        on_focus_source: Callable[[str], None],
        is_connected: Callable[[], bool],
        get_ga_names: Callable[[], dict[int, str]] | None = None,
        get_ga_dpts: Callable[[], dict[int, str]] | None = None,
    ) -> None:
        self._get_telegrams = get_telegrams
        self._get_cemi_records = get_cemi_records
        self._get_capture_state = get_capture_state
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_clear = on_clear
        self._on_focus_source = on_focus_source
        self._is_connected = is_connected
        self._get_ga_names = get_ga_names
        self._get_ga_dpts = get_ga_dpts
        # Rebuilt once per table frame so per-row lookups don't hit the project service repeatedly.
        # Keyed by raw group-address value (style-independent).
        self._ga_names: dict[int, str] = {}
        self._ga_dpts: dict[int, str] = {}
        self._selected: set[int] = set()
        self._last_selected: int = -1
        self._prev_scroll_max = 0.0
        self._filter_text = ""
        self._show_cemi = False

    def render(self) -> None:
        self._render_toolbar()
        # Nothing can be captured without a bus link: point the user at the Gateway menu instead of
        # leaving an empty table that looks broken.
        records = self._get_cemi_records() if self._show_cemi else self._get_telegrams()
        if not records and not self._is_connected():
            self._render_no_connection_hint()
            return
        if self._show_cemi:
            self._render_cemi_table()
        else:
            self._render_table()

    def _render_no_connection_hint(self) -> None:
        imgui.dummy(px_vec2(0, 8))
        imgui.push_style_color(
            imgui.Col_.text, imgui.ImVec4(0.9, 0.7, 0.3, 1.0)
        )  # amber
        imgui.text_wrapped(S.NO_CONNECTION_HINT)
        imgui.pop_style_color()

    def _apply_autoscroll(self) -> None:
        """Stick to the newest row as telegrams arrive, but stop once the user scrolls up.

        "Following" is measured against the PREVIOUS frame's scroll maximum (before new rows grew
        it), so a manual scroll-up is detected even while rows stream in every frame. Auto-scroll
        resumes automatically when the user scrolls back to the bottom."""
        following = imgui.get_scroll_y() >= self._prev_scroll_max - px(4.0)
        # Scroll to the content bottom via the scroll maximum, NOT set_scroll_here_y: with
        # ListClipper the last row is not submitted when off-screen, so set_scroll_here_y would only
        # reach the last VISIBLE row. This also keeps working once the capture deque is full and the
        # row count plateaus (a "new rows arrived" count guard would stop scrolling then).
        if following:
            imgui.set_scroll_y(imgui.get_scroll_max_y())
        self._prev_scroll_max = imgui.get_scroll_max_y()

    def _render_record_button(self, state: CaptureState) -> None:
        draw_list = imgui.get_window_draw_list()
        cursor = imgui.get_cursor_screen_pos()
        text_height = imgui.get_text_line_height()
        style = imgui.get_style()

        dot_radius = px(5)
        dot_gap = px(6)
        dot_center = imgui.ImVec2(
            cursor.x + style.frame_padding.x + dot_radius,
            cursor.y + text_height / 2 + style.frame_padding.y,
        )

        max_text_width = imgui.calc_text_size(S.BTN_RECORDING).x
        button_width = (
            style.frame_padding.x * 2 + dot_radius * 2 + dot_gap + max_text_width
        )
        text_pos = imgui.ImVec2(
            cursor.x + style.frame_padding.x + dot_radius * 2 + dot_gap,
            cursor.y + style.frame_padding.y,
        )

        if state == CaptureState.CAPTURING:
            pulse = 0.5 + 0.5 * math.sin(imgui.get_time() * 3.0)
            alpha = 0.5 + 0.5 * pulse
            draw_list.add_circle_filled(
                dot_center, dot_radius, color_u32(0.9, 0.2, 0.2, alpha)
            )
            draw_list.add_circle_filled(
                dot_center,
                dot_radius + px(pulse * 3),
                color_u32(0.9, 0.2, 0.2, 0.15 * (1 - pulse)),
            )

            if imgui.invisible_button(
                "##record",
                imgui.ImVec2(button_width, text_height + style.frame_padding.y * 2),
            ):
                self._on_stop()

            if imgui.is_item_hovered():
                draw_list.add_text(text_pos, color_u32(0.9, 0.3, 0.3, 1.0), S.BTN_STOP)
            else:
                draw_list.add_text(
                    text_pos, color_u32(0.9, 0.3, 0.3, 1.0), S.BTN_RECORDING
                )
        else:
            draw_list.add_circle_filled(
                dot_center, dot_radius, color_u32(0.5, 0.5, 0.5, 1.0)
            )

            if imgui.invisible_button(
                "##record",
                imgui.ImVec2(button_width, text_height + style.frame_padding.y * 2),
            ):
                self._on_start()

            draw_list.add_text(
                text_pos, imgui.get_color_u32(imgui.Col_.text), S.BTN_RECORD
            )

    def _render_toolbar(self) -> None:
        state = self._get_capture_state()
        self._render_record_button(state)

        imgui.same_line()
        imgui.set_next_item_width(px(150))
        _, self._filter_text = imgui.input_text_with_hint(
            "##filter", "Filter...", self._filter_text
        )

        imgui.same_line()
        records = self._get_cemi_records() if self._show_cemi else self._get_telegrams()
        count = len(records)
        selected_count = len(self._selected)
        if selected_count > 0:
            imgui.text(f"{selected_count}/{count}")
        else:
            imgui.text_disabled(str(count))

        imgui.same_line()
        if imgui.button(S.BTN_COPY):
            self._copy_telegrams()
        imgui.same_line()
        if imgui.button(S.BTN_CLEAR):
            self._clear_telegrams()

        imgui.same_line()
        imgui.text_disabled("|")
        imgui.same_line()
        if imgui.radio_button("Telegrams", not self._show_cemi):
            self._show_cemi = False
            self._selected.clear()
        imgui.same_line()
        if imgui.radio_button("CEMI", self._show_cemi):
            self._show_cemi = True
            self._selected.clear()

    def _render_table(self) -> None:
        telegrams = self._get_telegrams()
        # Group-address names from the loaded project (empty when none): shown on the destination
        # and searchable. Rebuilt once per frame here so row rendering only does dict lookups.
        self._ga_names = self._get_ga_names() if self._get_ga_names else {}
        self._ga_dpts = self._get_ga_dpts() if self._get_ga_dpts else {}
        if self._filter_text:
            filter_lower = self._filter_text.lower()
            telegrams = [
                t
                for t in telegrams
                if filter_lower in t.source.lower()
                or filter_lower in t.destination.lower()
                or filter_lower in t.value.lower()
                or filter_lower in t.service.lower()
                or filter_lower in self._ga_names.get(t.destination_raw, "").lower()
            ]

        avail = imgui.get_content_region_avail()
        flags = (
            imgui.TableFlags_.row_bg
            | imgui.TableFlags_.scroll_y
            | imgui.TableFlags_.borders_inner_h
        )
        if not imgui.begin_table(
            "##telegrams", 8, flags, imgui.ImVec2(avail.x, avail.y)
        ):
            return

        imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_setup_column("Time", imgui.TableColumnFlags_.width_fixed, px(70))
        imgui.table_setup_column(
            "", imgui.TableColumnFlags_.width_fixed, px(12)
        )  # service-color dot
        imgui.table_setup_column(
            "Via", imgui.TableColumnFlags_.width_fixed, px(12)
        )  # source-color square
        imgui.table_setup_column("Source", imgui.TableColumnFlags_.width_fixed, px(60))
        # Wider so the resolved group-address name (project loaded) fits next to the address.
        imgui.table_setup_column("Dest", imgui.TableColumnFlags_.width_fixed, px(160))
        imgui.table_setup_column("TPCI", imgui.TableColumnFlags_.width_fixed, px(50))
        imgui.table_setup_column("APCI", imgui.TableColumnFlags_.width_fixed, px(200))
        imgui.table_setup_column("Value", imgui.TableColumnFlags_.width_stretch)
        imgui.table_headers_row()

        # ListClipper renders only the visible rows, so a full 10k-row capture costs O(visible)
        # per frame instead of O(all) (and each row's timestamp is cached, not re-formatted).
        clipper = imgui.ListClipper()
        clipper.begin(len(telegrams))
        while clipper.step():
            for i in range(clipper.display_start, clipper.display_end):
                self._render_row(i, telegrams[i])

        self._apply_autoscroll()

        imgui.end_table()
        self._handle_shortcuts()

    def _render_row(self, index: int, telegram: TelegramRecord) -> None:
        imgui.table_next_row()

        color = SERVICE_COLORS.get(telegram.service, DEFAULT_SERVICE_COLOR)
        selected = index in self._selected
        source_color = SOURCE_COLORS.get(telegram.source_type)

        imgui.table_set_column_index(0)
        flags = (
            imgui.SelectableFlags_.span_all_columns
            | imgui.SelectableFlags_.allow_overlap
        )
        if imgui.selectable(f"{telegram.timestamp_str}##row{index}", selected, flags)[
            0
        ]:
            self._handle_click(index, telegram)

        imgui.table_set_column_index(1)
        draw_list = imgui.get_window_draw_list()
        cursor = imgui.get_cursor_screen_pos()
        center_y = cursor.y + imgui.get_text_line_height() / 2
        draw_list.add_circle_filled(
            imgui.ImVec2(cursor.x + px(3), center_y),
            px(3),
            color_u32(*color),
        )
        imgui.dummy(px_vec2(8, 0))

        # Via: telegram source as colored square
        imgui.table_set_column_index(2)
        if source_color is not None:
            cursor2 = imgui.get_cursor_screen_pos()
            half = imgui.get_text_line_height() / 2
            cx = cursor2.x + px(3)
            cy = cursor2.y + half
            draw_list.add_rect_filled(
                imgui.ImVec2(cx - px(3), cy - px(3)),
                imgui.ImVec2(cx + px(3), cy + px(3)),
                color_u32(*source_color),
            )
        imgui.dummy(px_vec2(8, 0))

        imgui.table_set_column_index(3)
        if source_color is not None:
            imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(*source_color, 1.0))
            imgui.text_disabled(telegram.source)
            imgui.pop_style_color()
        else:
            imgui.text_disabled(telegram.source)

        imgui.table_set_column_index(4)
        imgui.text(telegram.destination)
        ga_name = self._ga_names.get(telegram.destination_raw)
        if ga_name:
            imgui.same_line(0, px(6))
            imgui.text_disabled(ga_name)
            if imgui.is_item_hovered():
                imgui.set_tooltip(ga_name)

        imgui.table_set_column_index(5)
        tpci_abbrev = TPCI_ABBREV.get(
            telegram.tpci, telegram.tpci[:5] if telegram.tpci else ""
        )
        imgui.text_disabled(tpci_abbrev)

        imgui.table_set_column_index(6)
        imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(*color, 1.0))
        imgui.text(telegram.service)
        imgui.pop_style_color()

        imgui.table_set_column_index(7)
        if telegram.dpt:
            imgui.text_disabled(f"[{telegram.dpt}]")
            imgui.same_line(0, px(4))
        # Decode with the project group address' DPT when the frame itself carried none.
        value = telegram.value_with_dpt(self._ga_dpts.get(telegram.destination_raw))
        imgui.text(value if value else "-")

    def _render_cemi_table(self) -> None:
        records = self._get_cemi_records()
        if self._filter_text:
            filter_lower = self._filter_text.lower()
            records = [
                r
                for r in records
                if filter_lower in r.src_addr.lower()
                or filter_lower in r.dst_addr.lower()
                or filter_lower in r.msg_code.lower()
            ]

        avail = imgui.get_content_region_avail()
        flags = (
            imgui.TableFlags_.row_bg
            | imgui.TableFlags_.scroll_y
            | imgui.TableFlags_.borders_inner_h
        )
        if not imgui.begin_table("##cemi", 8, flags, imgui.ImVec2(avail.x, avail.y)):
            return

        imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_setup_column("Time", imgui.TableColumnFlags_.width_fixed, px(70))
        imgui.table_setup_column("Via", imgui.TableColumnFlags_.width_fixed, px(12))
        imgui.table_setup_column("Code", imgui.TableColumnFlags_.width_fixed, px(90))
        imgui.table_setup_column("From", imgui.TableColumnFlags_.width_fixed, px(55))
        imgui.table_setup_column("To", imgui.TableColumnFlags_.width_fixed, px(60))
        imgui.table_setup_column("Flg", imgui.TableColumnFlags_.width_fixed, px(50))
        imgui.table_setup_column("Hops", imgui.TableColumnFlags_.width_fixed, px(35))
        imgui.table_setup_column("Raw", imgui.TableColumnFlags_.width_stretch)
        imgui.table_headers_row()

        clipper = imgui.ListClipper()
        clipper.begin(len(records))
        while clipper.step():
            for i in range(clipper.display_start, clipper.display_end):
                self._render_cemi_row(i, records[i])

        self._apply_autoscroll()

        imgui.end_table()

    def _render_cemi_row(self, index: int, rec: CemiRecord) -> None:
        imgui.table_next_row()
        source_color = SOURCE_COLORS.get(rec.source_type)

        imgui.table_set_column_index(0)
        imgui.text_disabled(rec.timestamp_str)

        # Via: cemi frame source as colored square
        imgui.table_set_column_index(1)
        draw_list = imgui.get_window_draw_list()
        cursor = imgui.get_cursor_screen_pos()
        if source_color is not None:
            half = imgui.get_text_line_height() / 2
            cx = cursor.x + px(3)
            cy = cursor.y + half
            draw_list.add_rect_filled(
                imgui.ImVec2(cx - px(3), cy - px(3)),
                imgui.ImVec2(cx + px(3), cy + px(3)),
                color_u32(*source_color),
            )
        imgui.dummy(px_vec2(8, 0))

        imgui.table_set_column_index(2)
        imgui.text_disabled(rec.msg_code)

        imgui.table_set_column_index(3)
        imgui.text_disabled(rec.src_addr)

        imgui.table_set_column_index(4)
        imgui.text(rec.dst_addr)

        imgui.table_set_column_index(5)
        if rec.flags is not None:
            imgui.text_disabled(f"{rec.flags:04x}")
        else:
            imgui.text_disabled("-")

        imgui.table_set_column_index(6)
        if rec.hops is not None:
            imgui.text_disabled(str(rec.hops))
        else:
            imgui.text_disabled("-")

        imgui.table_set_column_index(7)
        imgui.text_disabled(rec.raw_hex)

    def _handle_click(self, index: int, telegram: TelegramRecord) -> None:
        io = imgui.get_io()
        ctrl = io.key_ctrl or io.key_super
        shift = io.key_shift

        if shift and self._last_selected >= 0:
            self._select_range(self._last_selected, index, additive=ctrl)
        elif ctrl:
            self._toggle(index)
        else:
            self._select_single(index, telegram)

    def _handle_shortcuts(self) -> None:
        if not imgui.is_window_focused():
            return
        io = imgui.get_io()
        if (io.key_ctrl or io.key_super) and imgui.is_key_pressed(imgui.Key.c):
            self._copy_telegrams()
        if imgui.is_key_pressed(imgui.Key.escape):
            self._selected.clear()

    def _select_range(self, start: int, end: int, additive: bool) -> None:
        if not additive:
            self._selected.clear()
        lo, hi = min(start, end), max(start, end)
        self._selected.update(range(lo, hi + 1))

    def _toggle(self, index: int) -> None:
        self._selected.symmetric_difference_update({index})
        self._last_selected = index

    def _select_single(self, index: int, telegram: TelegramRecord) -> None:
        self._selected = {index}
        self._last_selected = index
        self._on_focus_source(telegram.source)

    def _copy_telegrams(self) -> None:
        telegrams = self._get_telegrams()
        if self._selected:
            indices = sorted(self._selected)
        else:
            indices = list(range(len(telegrams)))
        if not indices:
            return

        header = "Time\tVia\tSource\tDestination\tTPCI\tAPCI\tDPT\tValue"
        rows = [
            self._telegram_to_row(telegrams[i]) for i in indices if i < len(telegrams)
        ]
        imgui.set_clipboard_text("\n".join([header, *rows]))

    def _telegram_to_row(self, t: TelegramRecord) -> str:
        return f"{t.timestamp_str}\t{t.source_type.value}\t{t.source}\t{t.destination}\t{t.tpci}\t{t.service}\t{t.dpt}\t{t.value}"

    def _clear_telegrams(self) -> None:
        self._selected.clear()
        self._on_clear()
