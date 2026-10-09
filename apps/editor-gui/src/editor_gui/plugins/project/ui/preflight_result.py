"""A result window for a device pre-flight (dry run).

Shows whether the device already matches the configuration and, per memory segment / property,
how many bytes would change. The pre-flight runs on a background thread; its result is handed in via
:meth:`submit_result` / :meth:`submit_error` (thread-safe) and picked up on the next UI frame. The
window can export the current (Ist) and planned (Soll) bytes of every location to a text file.
"""

from __future__ import annotations

import contextlib
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.project.strings import S
from editor_gui.widgets.dpi import px, px_vec2

if TYPE_CHECKING:
    from xknxeditor.download.preflight import PreflightReport, SegmentDiff

_GREEN = imgui.ImVec4(0.4, 0.85, 0.45, 1.0)
_ORANGE = imgui.ImVec4(0.95, 0.75, 0.2, 1.0)
_RED = imgui.ImVec4(0.9, 0.35, 0.35, 1.0)
_BLUE = imgui.ImVec4(0.45, 0.7, 0.95, 1.0)
_TEAL = imgui.ImVec4(0.4, 0.8, 0.75, 1.0)

# How a changed location is classified, most to least actionable.
_CAT_CONFIG = "config"  # a changed bit carries a real configured parameter value
_CAT_DEFAULT = "default"  # changed bits only reset to the application default (benign)
_CAT_RUNTIME = "runtime"  # device-managed byte the firmware sets itself (benign)
_CAT_MATCH = "match"


class PreflightResultWindow:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Set from the worker thread, consumed on the UI thread.
        self._pending: (
            tuple[
                str,
                str,
                PreflightReport | None,
                str | None,
                frozenset[int],
                dict[int, int],
            ]
            | None
        ) = None
        self._label = ""
        self._scope = ""
        self._report: PreflightReport | None = None
        self._error: str | None = None
        # Absolute addresses of device-managed (runtime) bytes, e.g. a download
        # detection byte the firmware resets after a download; a difference there
        # is benign, so those locations are annotated instead of flagged.
        self._runtime: frozenset[int] = frozenset()
        # Absolute address -> bitmask of bits an active parameter actually writes.
        # A changed bit outside this mask is only reset to the application default
        # (the device holds an older value), which is benign, not a configured change.
        self._driven: dict[int, int] = {}
        self._show = False
        self._save_path_buf = "preflight.txt"

    # -- worker thread: no imgui here -------------------------------------
    def submit_result(
        self,
        label: str,
        scope: str,
        report: PreflightReport,
        runtime_addresses: set[int] | frozenset[int] = frozenset(),
        driven_bits: dict[int, int] | None = None,
    ) -> None:
        with self._lock:
            self._pending = (
                label,
                scope,
                report,
                None,
                frozenset(runtime_addresses),
                dict(driven_bits or {}),
            )

    def submit_error(self, label: str, scope: str, error: str) -> None:
        with self._lock:
            self._pending = (label, scope, None, error, frozenset(), {})

    # -- UI thread --------------------------------------------------------
    def render(self) -> None:
        with self._lock:
            if self._pending is not None:
                (
                    self._label,
                    self._scope,
                    self._report,
                    self._error,
                    self._runtime,
                    self._driven,
                ) = self._pending
                self._pending = None
                self._show = True
        if not self._show:
            return
        imgui.set_next_window_size(px_vec2(720, 520), imgui.Cond_.first_use_ever)
        opened, p_open = imgui.begin(S.PREFLIGHT_RESULT_TITLE, self._show)
        if p_open is not None:
            self._show = p_open
        if opened:
            self._render_body()
        imgui.end()

    def _render_body(self) -> None:
        imgui.text_disabled(f"{self._label}    scope={self._scope}")
        imgui.same_line()
        # Text is plain (not selectable), so offer explicit copy-to-clipboard.
        if imgui.small_button(S.COPY_LOG):
            imgui.set_clipboard_text(self._clipboard_text())
        imgui.separator()
        # Reassure up front: a test never writes to the device. (text_disabled doesn't wrap, so
        # wrap manually with the disabled color, or the long sentence overflows the window width.)
        imgui.push_style_color(
            imgui.Col_.text, imgui.get_style_color_vec4(imgui.Col_.text_disabled)
        )
        imgui.text_wrapped(S.PREFLIGHT_NO_CHANGES_MADE)
        imgui.pop_style_color()
        # The schema-23 precondition is easy to miss but decisive: a schema-20 device shows huge
        # false diffs. Render it as a coloured callout, not dim text, so it stands out.
        imgui.push_style_color(imgui.Col_.text, _BLUE)
        imgui.text_wrapped(S.PREFLIGHT_ETS6_NOTE)
        imgui.pop_style_color()
        imgui.spacing()

        if self._error is not None:
            imgui.push_style_color(imgui.Col_.text, _RED)
            imgui.text_wrapped(f"{S.PREFLIGHT_FAILED}: {self._error}")
            imgui.pop_style_color()
            return

        report = self._report
        if report is None:
            return

        # Break the changed bytes into three buckets so a difference count is never
        # scary without context: real configured changes vs. bytes only reset to the
        # application default (device holds an older value) vs. device-managed runtime
        # bytes. The last two are benign; only the first is a change you made.
        config_bytes = default_bytes = runtime_bytes = 0
        config_locations = 0
        for segment in report.segments:
            if not segment.changed:
                continue
            c, d, r = self._segment_byte_counts(segment)
            config_bytes += c
            default_bytes += d
            runtime_bytes += r
            if c:
                config_locations += 1
        # Property writes are always real configured values (flags, tables).
        config_bytes += sum(p.changed_bytes for p in report.changed_properties)
        config_locations += len(report.changed_properties)

        if config_locations:
            imgui.push_style_color(imgui.Col_.text, _ORANGE)
            imgui.text(
                S.PREFLIGHT_WOULD_CHANGE.format(
                    bytes=config_bytes, locations=config_locations
                )
            )
            imgui.pop_style_color()
            imgui.push_style_color(
                imgui.Col_.text, imgui.get_style_color_vec4(imgui.Col_.text_disabled)
            )
            imgui.text_wrapped(S.PREFLIGHT_ETS5_HINT)
            imgui.pop_style_color()
        elif default_bytes or runtime_bytes:
            # No configured differences: the device carries the configuration. The
            # remaining diffs are the benign buckets explained below, so say so
            # plainly instead of claiming a byte-identical match.
            imgui.push_style_color(imgui.Col_.text, _GREEN)
            imgui.text_wrapped(S.PREFLIGHT_ONLY_BENIGN)
            imgui.pop_style_color()
        else:
            imgui.push_style_color(imgui.Col_.text, _GREEN)
            imgui.text(S.PREFLIGHT_MATCH)
            imgui.pop_style_color()
        if default_bytes:
            imgui.push_style_color(imgui.Col_.text, _TEAL)
            imgui.text_wrapped(S.PREFLIGHT_DEFAULT_NOTE.format(bytes=default_bytes))
            imgui.pop_style_color()
        if runtime_bytes:
            imgui.text_disabled(S.PREFLIGHT_RUNTIME_NOTE.format(bytes=runtime_bytes))

        matched = sum(1 for s in report.segments if not s.changed) + sum(
            1 for p in report.properties if not p.changed
        )
        imgui.text_disabled(
            S.PREFLIGHT_SUMMARY_COUNTS.format(matched=matched, changed=config_locations)
        )

        if imgui.button(S.PREFLIGHT_EXPORT):
            imgui.open_popup("##preflight_export")
        self._render_export_modal(report)
        imgui.separator()
        self._render_table(report)

    def _render_table(self, report: PreflightReport) -> None:
        flags = (
            imgui.TableFlags_.row_bg
            | imgui.TableFlags_.borders_inner_h
            | imgui.TableFlags_.scroll_y
        )
        avail = imgui.get_content_region_avail()
        if not imgui.begin_table(
            "##preflight", 4, flags, imgui.ImVec2(avail.x, avail.y)
        ):
            return
        imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_setup_column(S.PREFLIGHT_COL_LOCATION)
        imgui.table_setup_column(
            S.PREFLIGHT_COL_SIZE, imgui.TableColumnFlags_.width_fixed, px(70)
        )
        imgui.table_setup_column(
            S.PREFLIGHT_COL_STATUS, imgui.TableColumnFlags_.width_fixed, px(120)
        )
        imgui.table_setup_column(
            S.PREFLIGHT_COL_CHANGED, imgui.TableColumnFlags_.width_fixed, px(90)
        )
        imgui.table_headers_row()

        for segment in report.segments:
            self._render_row(
                S.PREFLIGHT_MEM_LABEL.format(address=f"{segment.address:#06x}"),
                len(segment.planned),
                segment.changed,
                segment.changed_bytes,
                category=self._segment_category(segment),
            )
        for prop in report.properties:
            self._render_row(
                S.PREFLIGHT_PROP_LABEL.format(
                    object=prop.object_index, property=prop.property_id
                ),
                len(prop.planned),
                prop.changed,
                prop.changed_bytes,
                category=_CAT_CONFIG if prop.changed else _CAT_MATCH,
            )
        imgui.end_table()

    def _byte_category(self, address: int, changed_bits: int) -> str:
        """Classify one changed byte from the bits that differ from the device.

        A device-managed (runtime) byte is benign; otherwise, a changed bit that an
        active parameter writes is a real configured change, while a changed bit no
        parameter drives is only reset to the application default (the device holds
        an older value) and is likewise benign.
        """
        if address in self._runtime:
            return _CAT_RUNTIME
        if changed_bits & self._driven.get(address, 0):
            return _CAT_CONFIG
        return _CAT_DEFAULT

    @staticmethod
    def _changed_bits(segment: SegmentDiff, offset: int) -> int:
        """Bits that differ from the device at ``offset`` within ``segment``."""
        planned = segment.planned[offset]
        current = segment.current[offset] if offset < len(segment.current) else 0
        return planned ^ current

    def _segment_byte_counts(self, segment: SegmentDiff) -> tuple[int, int, int]:
        """Return ``(config, default, runtime)`` changed-byte counts for ``segment``."""
        config = default = runtime = 0
        for r in segment.changed_ranges:
            for i in range(r.length):
                offset = r.start + i
                category = self._byte_category(
                    segment.address + offset, self._changed_bits(segment, offset)
                )
                if category == _CAT_CONFIG:
                    config += 1
                elif category == _CAT_RUNTIME:
                    runtime += 1
                else:
                    default += 1
        return config, default, runtime

    def _segment_category(self, segment: SegmentDiff) -> str:
        """The segment's overall category, taking the most actionable of its bytes."""
        if not segment.changed:
            return _CAT_MATCH
        config, default, runtime = self._segment_byte_counts(segment)
        if config:
            return _CAT_CONFIG
        if default:
            return _CAT_DEFAULT
        if runtime:
            return _CAT_RUNTIME
        return _CAT_MATCH

    def _render_row(
        self,
        location: str,
        size: int,
        changed: bool,
        changed_bytes: int,
        category: str = _CAT_MATCH,
    ) -> None:
        imgui.table_next_row()
        imgui.table_set_column_index(0)
        imgui.text(location)
        imgui.table_set_column_index(1)
        imgui.text_disabled(f"{size} B")
        imgui.table_set_column_index(2)
        if category == _CAT_RUNTIME:
            self._status_cell(
                _BLUE, S.PREFLIGHT_STATUS_RUNTIME, S.PREFLIGHT_RUNTIME_TOOLTIP
            )
        elif category == _CAT_DEFAULT:
            self._status_cell(
                _TEAL, S.PREFLIGHT_STATUS_DEFAULT, S.PREFLIGHT_DEFAULT_TOOLTIP
            )
        elif category == _CAT_CONFIG:
            self._status_cell(_ORANGE, S.PREFLIGHT_STATUS_CHANGE, None)
        else:
            self._status_cell(_GREEN, S.PREFLIGHT_STATUS_MATCH, None)
        imgui.table_set_column_index(3)
        imgui.text_disabled(str(changed_bytes) if changed else "-")

    @staticmethod
    def _status_cell(color: imgui.ImVec4, label: str, tooltip: str | None) -> None:
        imgui.push_style_color(imgui.Col_.text, color)
        imgui.text(label)
        imgui.pop_style_color()
        if tooltip is not None and imgui.is_item_hovered():
            imgui.set_tooltip(tooltip)

    def _render_export_modal(self, report: PreflightReport) -> None:
        imgui.set_next_window_size(px_vec2(520, 0), imgui.Cond_.always)
        if not imgui.begin_popup_modal(
            "##preflight_export",
            None,
            imgui.WindowFlags_.no_title_bar | imgui.WindowFlags_.always_auto_resize,
        )[0]:
            return
        imgui.text(S.PREFLIGHT_EXPORT_PATH)
        imgui.set_next_item_width(-1)
        _, self._save_path_buf = imgui.input_text("##pf_path", self._save_path_buf)
        imgui.spacing()
        btn_w = px_vec2(120, 0)
        if imgui.button(S.BTN_SAVE, btn_w):
            with contextlib.suppress(OSError):
                Path(self._save_path_buf).write_text(
                    self._export_text(report), encoding="utf-8"
                )
            imgui.close_current_popup()
        imgui.same_line()
        if imgui.button(S.BTN_CANCEL, btn_w):
            imgui.close_current_popup()
        imgui.end_popup()

    def _clipboard_text(self) -> str:
        header = f"{self._label} scope={self._scope}"
        if self._error is not None:
            return f"{header}\n{S.PREFLIGHT_FAILED}: {self._error}"
        if self._report is not None:
            return f"{header}\n{self._export_text(self._report)}"
        return header

    def _export_text(self, report: PreflightReport) -> str:
        lines = [
            f"# Pre-flight {self._label} scope={self._scope} "
            f"generated={datetime.now().isoformat(timespec='seconds')}",
            report.summary(),
            "",
        ]
        for segment in report.segments:
            lines.append(
                f"## memory {segment.address:#06x} ({len(segment.planned)}B) "
                f"changed={segment.changed_bytes}"
            )
            lines.append(f"ist : {segment.current.hex()}")
            lines.append(f"soll: {segment.planned.hex()}")
        for prop in report.properties:
            lines.append(
                f"## object {prop.object_index} property {prop.property_id} "
                f"changed={prop.changed_bytes}"
            )
            lines.append(f"ist : {prop.current.hex()}")
            lines.append(f"soll: {prop.planned.hex()}")
        return "\n".join(lines) + "\n"
