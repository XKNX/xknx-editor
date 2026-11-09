"""Aggregate the Group Monitor's telegrams: who sends how much, and to which group address.

The aggregation (:func:`compute`) is pure and shared by the Statistics tab and the MCP tool, so
both read the same numbers off the same log.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugins.monitor.formatting import ProjectIndex
from editor_gui.plugins.monitor.frames import individual_address_text
from editor_gui.plugins.monitor.records import TICKS_PER_SECOND
from editor_gui.plugins.monitor.service import MonitorKind
from editor_gui.plugins.monitor.strings import S
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.dpi import px
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from editor_gui.widgets.text_util import text_clipped_tooltip

if TYPE_CHECKING:
    from editor_gui.plugins.monitor.records import MonitorRecord
    from editor_gui.plugins.monitor.service import MonitorService


@dataclass(frozen=True, slots=True)
class AddressCount:
    """How many telegrams one address (a source device or a destination group) accounts for."""

    address: str
    name: str
    description: str
    count: int


@dataclass(slots=True)
class Statistics:
    """The aggregated group-telegram traffic of one log."""

    total: int = 0
    span_seconds: float = 0.0
    per_minute: float = 0.0
    # The busiest clock-minute: its telegram count and the minute's start (0 ticks = none).
    peak_per_minute: int = 0
    peak_ticks: int = 0
    sources: list[AddressCount] = field(default_factory=list[AddressCount])
    destinations: list[AddressCount] = field(default_factory=list[AddressCount])


def _ranked(
    counts: dict[int, int], names: dict[int, tuple[str, str, str]]
) -> list[AddressCount]:
    """Turn raw counts into :class:`AddressCount`s, most frequent first, then by address."""
    items = [
        AddressCount(names[raw][0], names[raw][1], names[raw][2], count)
        for raw, count in counts.items()
    ]
    items.sort(key=lambda item: (-item.count, item.address))
    return items


def compute(records: Sequence[MonitorRecord], index: ProjectIndex) -> Statistics:
    """Aggregate the group telegrams in ``records`` by source device and destination group.

    Events (start/stop/comment), undecodable frames and point-to-point frames are ignored; only
    group telegrams count. Names are resolved the same way the monitor table resolves them, so a
    source/destination reads as it does in the log."""
    source_counts: dict[int, int] = {}
    source_names: dict[int, tuple[str, str, str]] = {}
    dest_counts: dict[int, int] = {}
    dest_names: dict[int, tuple[str, str, str]] = {}
    minute_counts: dict[int, int] = {}
    ticks_per_minute = TICKS_PER_SECOND * 60
    total = 0
    first_ticks: int | None = None
    last_ticks = 0
    for record in records:
        frame = record.frame
        if frame is None or not frame.is_data or not frame.group:
            continue
        total += 1
        if first_ticks is None:
            first_ticks = record.ticks
        last_ticks = record.ticks
        minute = record.ticks // ticks_per_minute
        minute_counts[minute] = minute_counts.get(minute, 0) + 1
        source = frame.source or 0
        source_counts[source] = source_counts.get(source, 0) + 1
        if source not in source_names:
            device = index.devices.get(source)
            source_names[source] = (
                individual_address_text(source),
                device.name if device is not None else "",
                device.description if device is not None else "",
            )
        destination = frame.destination or 0
        dest_counts[destination] = dest_counts.get(destination, 0) + 1
        if destination not in dest_names:
            entry = index.group_addresses.get(destination)
            dest_names[destination] = (
                index.group_address_text(destination),
                entry.name if entry is not None else "",
                entry.description if entry is not None else "",
            )
    span_seconds = (
        (last_ticks - first_ticks) / TICKS_PER_SECOND
        if first_ticks is not None
        else 0.0
    )
    per_minute = total / span_seconds * 60 if span_seconds > 0 else 0.0
    peak_minute = max(minute_counts, key=lambda m: minute_counts[m], default=None)
    return Statistics(
        total=total,
        span_seconds=span_seconds,
        per_minute=per_minute,
        peak_per_minute=minute_counts[peak_minute] if peak_minute is not None else 0,
        peak_ticks=peak_minute * ticks_per_minute if peak_minute is not None else 0,
        sources=_ranked(source_counts, source_names),
        destinations=_ranked(dest_counts, dest_names),
    )


def _span_text(seconds: float) -> str:
    """A short h/m/s span like ``1h 05m`` or ``42s``."""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return S.STATS_SPAN_HMS.format(hours=hours, minutes=f"{minutes:02d}")
    if minutes:
        return S.STATS_SPAN_MS.format(minutes=minutes, seconds=f"{secs:02d}")
    return S.STATS_SPAN_S.format(seconds=secs)


class StatisticsView:
    """The Statistics tab: a summary line and the Top Sources / Top Destinations tables.

    The aggregation is cached by the log's (generation, length) and the project index, so it is
    recomputed only when the telegrams or the names change, not every frame."""

    def __init__(
        self,
        service: MonitorService,
        persist_get: Callable[[], bool] | None = None,
        persist_set: Callable[[bool], None] | None = None,
    ) -> None:
        self._service = service
        self._persist_get = persist_get
        self._persist_set = persist_set
        self._cache_key: object = None
        self._stats = Statistics()

    def _current(self, index: ProjectIndex) -> Statistics:
        log = self._service.channels[MonitorKind.GROUP].log
        key = (log.generation, len(log), index.revision)
        if key != self._cache_key:
            self._stats = compute(log.snapshot(), index)
            self._cache_key = key
        return self._stats

    def _render_persist_toggle(self, *, after_text: bool = False) -> bool:
        """The Persist toggle (shared with the Group Monitor); True when it was drawn. With
        ``after_text`` it follows the preceding text on the same line, centred to it."""
        if self._persist_get is None or self._persist_set is None:
            return False
        if after_text:
            imgui.same_line()
        changed, value = checkbox(S.TOOL_PERSIST, self._persist_get(), centred=True)
        if changed:
            self._persist_set(value)
        imgui.set_item_tooltip(S.TOOL_PERSIST_HINT)
        return True

    def render(self, index: ProjectIndex) -> None:
        stats = self._current(index)
        imgui.align_text_to_frame_padding()
        if stats.total == 0:
            imgui.text_disabled(S.STATS_EMPTY)
            self._render_persist_toggle(after_text=True)
            return
        imgui.text_disabled(S.STATS_TOTAL(stats.total).format(count=stats.total))
        imgui.same_line()
        imgui.text_disabled(S.STATS_RATE.format(rate=f"{stats.per_minute:.1f}"))
        if stats.peak_per_minute:
            imgui.same_line()
            moment = datetime.fromtimestamp(stats.peak_ticks // TICKS_PER_SECOND)
            when = f"{moment:%H:%M} {moment:%Y-%m-%d}"
            imgui.text_disabled(
                S.STATS_PEAK.format(peak=stats.peak_per_minute, when=when)
            )
        imgui.same_line()
        imgui.text_disabled(
            S.MONITOR_BUS_LOAD.format(rate=f"{self._service.bus_load():.1f}")
        )
        if stats.span_seconds > 0:
            imgui.same_line()
            imgui.text_disabled(
                S.STATS_SPAN.format(span=_span_text(stats.span_seconds))
            )
        self._render_persist_toggle(after_text=True)
        imgui.separator()
        avail = imgui.get_content_region_avail()
        height = max(avail.y / 2 - imgui.get_frame_height_with_spacing(), px(120.0))
        imgui.text(S.STATS_SOURCES)
        self._render_counts("##stats_sources", stats.sources, height)
        imgui.text(S.STATS_DESTINATIONS)
        self._render_counts("##stats_destinations", stats.destinations, 0.0)

    @staticmethod
    def _render_counts(table_id: str, rows: list[AddressCount], height: float) -> None:
        flags = (
            imgui.TableFlags_.borders_inner
            | imgui.TableFlags_.sizing_stretch_prop
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.scroll_y
            | imgui.TableFlags_.reorderable
        )
        size = imgui.ImVec2(0.0, height) if height > 0 else None
        if not begin_data_table(table_id, 4, flags, size):
            return
        stretch = imgui.TableColumnFlags_.width_stretch
        imgui.table_setup_column(S.STATS_ADDRESS, stretch, 0.2)
        imgui.table_setup_column(S.STATS_NAME, stretch, 0.4)
        imgui.table_setup_column(S.STATS_DESCRIPTION, stretch, 0.25)
        imgui.table_setup_column(S.STATS_COUNT, stretch, 0.15)
        data_table_headers()
        clipper = imgui.ListClipper()
        clipper.begin(len(rows))
        while clipper.step():
            for i in range(clipper.display_start, clipper.display_end):
                row = rows[i]
                imgui.table_next_row()
                imgui.table_set_column_index(0)
                imgui.text(row.address)
                imgui.table_set_column_index(1)
                text_clipped_tooltip(row.name)
                imgui.table_set_column_index(2)
                text_clipped_tooltip(row.description)
                imgui.table_set_column_index(3)
                imgui.text(str(row.count))
        imgui.end_table()
