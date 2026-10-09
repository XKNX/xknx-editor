from __future__ import annotations

from collections.abc import Callable

from imgui_bundle import imgui

from editor_gui.plugins.logger.service import LogRecord
from editor_gui.plugins.logger.strings import S
from editor_gui.widgets.dpi import px

LEVEL_COLORS: dict[str, tuple[float, float, float]] = {
    "debug": (0.5, 0.5, 0.55),
    "info": (0.85, 0.85, 0.85),
    "warning": (0.95, 0.75, 0.2),
    "error": (0.9, 0.35, 0.35),
    "critical": (1.0, 0.2, 0.2),
}
_DEFAULT_COLOR = (0.85, 0.85, 0.85)

_LEVEL_RANK = {"debug": 0, "info": 1, "warning": 2, "error": 3, "critical": 4}
# (label, minimum rank) options for the level filter dropdown. "Debug" (rank 0) is the lowest, so it
# shows every record incl. the package traces; the levels above it hide progressively more.
_LEVEL_OPTIONS = [("Debug", 0), ("Info", 1), ("Warning", 2), ("Error", 3)]


class LogPanel:
    def __init__(
        self,
        get_records: Callable[[], list[LogRecord]],
        on_clear: Callable[[], None],
    ) -> None:
        self._get_records = get_records
        self._on_clear = on_clear
        self._filter_text = ""
        self._prev_scroll_max = 0.0
        self._last_count = 0
        # Default to Info: the many DEBUG traces (programming/download/myknx/export) are captured but
        # hidden until the user selects "Debug", keeping normal operation readable.
        self._min_level_idx = 1  # index into _LEVEL_OPTIONS ("Info")

    def render(self) -> None:
        self._render_toolbar()
        self._render_table()

    def _filtered_records(self) -> list[LogRecord]:
        records = self._get_records()
        min_rank = _LEVEL_OPTIONS[self._min_level_idx][1]
        if min_rank:
            records = [r for r in records if _LEVEL_RANK.get(r.level, 1) >= min_rank]
        if not self._filter_text:
            return records
        fl = self._filter_text.lower()
        return [
            r
            for r in records
            if fl in r.event.lower()
            or fl in r.plugin.lower()
            or fl in r.level.lower()
            or any(fl in v.lower() for v in r.payload.values())
        ]

    def _record_as_text(self, record: LogRecord) -> str:
        line = (
            f"{record.timestamp_str} {record.level.upper()} "
            f"[{record.plugin}] {record.event}"
        )
        if record.payload:
            line += "  " + "  ".join(f"{k}={v}" for k, v in record.payload.items())
        return line

    def _render_toolbar(self) -> None:
        records = self._get_records()
        imgui.text_disabled(str(len(records)))
        imgui.same_line()
        imgui.set_next_item_width(px(200))
        _, self._filter_text = imgui.input_text_with_hint(
            "##logfilter", S.FILTER_PLACEHOLDER, self._filter_text
        )
        imgui.same_line()
        imgui.set_next_item_width(px(100))
        _, self._min_level_idx = imgui.combo(
            "##loglevel", self._min_level_idx, [label for label, _ in _LEVEL_OPTIONS]
        )
        imgui.same_line()
        # Rows are plain text (not selectable), so offer an explicit copy-all-to-clipboard.
        # Copy the COMPLETE log (all records, ignoring the current filter) so nothing is lost.
        if imgui.button(S.COPY_LOG) and records:
            text = "\n".join(self._record_as_text(r) for r in records)
            # Strip control chars (esp. NUL, which some payloads like a gateway name carry): a NUL
            # terminates the C string in set_clipboard_text and would truncate the whole copy.
            text = "".join(c for c in text if c in "\n\t" or c.isprintable())
            imgui.set_clipboard_text(text)
        imgui.same_line()
        if imgui.button(S.BTN_CLEAR):
            self._on_clear()
            self._last_count = 0

    def _render_table(self) -> None:
        records = self._filtered_records()

        avail = imgui.get_content_region_avail()
        flags = (
            imgui.TableFlags_.row_bg
            | imgui.TableFlags_.scroll_y
            | imgui.TableFlags_.borders_inner_h
        )
        if not imgui.begin_table("##logs", 4, flags, imgui.ImVec2(avail.x, avail.y)):
            return

        imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_setup_column(
            S.COL_TIME, imgui.TableColumnFlags_.width_fixed, px(95)
        )
        imgui.table_setup_column(
            S.COL_LEVEL, imgui.TableColumnFlags_.width_fixed, px(60)
        )
        imgui.table_setup_column(
            S.COL_PLUGIN, imgui.TableColumnFlags_.width_fixed, px(90)
        )
        imgui.table_setup_column(S.COL_MESSAGE, imgui.TableColumnFlags_.width_stretch)
        imgui.table_headers_row()

        for record in records:
            self._render_row(record)

        # Stick to the newest row, but stop once the user scrolls up (resume at the bottom).
        # "Following" is measured against the previous frame's scroll max (before new rows grew it),
        # so a manual scroll-up is detected even while records stream in.
        current_count = len(records)
        following = imgui.get_scroll_y() >= self._prev_scroll_max - px(4.0)
        if current_count > self._last_count and following:
            imgui.set_scroll_here_y(1.0)
        self._prev_scroll_max = imgui.get_scroll_max_y()
        self._last_count = current_count

        imgui.end_table()

    def _render_row(self, record: LogRecord) -> None:
        r, g, b = LEVEL_COLORS.get(record.level, _DEFAULT_COLOR)
        imgui.table_next_row()

        imgui.table_set_column_index(0)
        imgui.text_disabled(record.timestamp_str)

        imgui.table_set_column_index(1)
        imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(r, g, b, 1.0))
        imgui.text(record.level.upper())
        imgui.pop_style_color()

        imgui.table_set_column_index(2)
        imgui.text_disabled(record.plugin)

        imgui.table_set_column_index(3)
        imgui.push_style_color(imgui.Col_.text, imgui.ImVec4(r, g, b, 1.0))
        imgui.text(record.event)
        imgui.pop_style_color()
        if record.payload:
            payload_str = "  " + "  ".join(
                f"{k}={v}" for k, v in record.payload.items()
            )
            imgui.same_line(0, 0)
            imgui.text_disabled(payload_str)
