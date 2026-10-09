"""Value timeline: plot decoded numeric group-value telegrams over time (custom draw-list chart).

Dependency-free (no ImPlot/numpy): pin group addresses on the left, and their captured numeric
values are decoded with the project DPT and drawn as lines on the right."""

from __future__ import annotations

import time
from collections.abc import Callable
from itertools import pairwise
from typing import Any

from imgui_bundle import imgui

from editor_gui.dpt import transcoder_for
from editor_gui.plugins.timeline.strings import S
from editor_gui.widgets.dpi import px

# (raw group-address value, display label, dpt string)
GaInfo = tuple[int, str, str]

_PALETTE = [
    imgui.ImVec4(0.36, 0.71, 1.0, 1.0),
    imgui.ImVec4(0.45, 0.8, 0.5, 1.0),
    imgui.ImVec4(1.0, 0.75, 0.3, 1.0),
    imgui.ImVec4(0.9, 0.5, 0.9, 1.0),
    imgui.ImVec4(0.9, 0.45, 0.45, 1.0),
    imgui.ImVec4(0.5, 0.85, 0.85, 1.0),
]


class TimelinePanel:
    def __init__(
        self,
        get_telegrams: Callable[[], list[Any]],
        get_ga_info: Callable[[], list[GaInfo]],
        is_open: Callable[[], bool],
    ) -> None:
        self._get_telegrams = get_telegrams
        self._get_ga_info = get_ga_info
        self._is_open = is_open
        self._pinned: set[int] = set()
        self._filter = ""
        self._window_s = 0.0  # 0 = all

    def render(self) -> None:
        if not self._is_open():
            imgui.text_disabled(S.TIMELINE_EMPTY)
            return
        ga_info = self._get_ga_info()
        color_by_raw = {
            raw: _PALETTE[i % len(_PALETTE)] for i, (raw, _l, _d) in enumerate(ga_info)
        }
        avail = imgui.get_content_region_avail()
        if imgui.begin_child("##tl_list", imgui.ImVec2(px(240.0), avail.y), True):
            self._render_pin_list(ga_info, color_by_raw)
        imgui.end_child()
        imgui.same_line()
        if imgui.begin_child("##tl_chart", imgui.ImVec2(0.0, 0.0), True):
            self._render_chart(ga_info, color_by_raw)
        imgui.end_child()

    def _render_pin_list(
        self, ga_info: list[GaInfo], color_by_raw: dict[int, imgui.ImVec4]
    ) -> None:
        from editor_gui.widgets.filter_box import filter_box

        self._filter = filter_box("##tl_filter", S.TIMELINE_SEARCH, self._filter)
        needle = self._filter.lower().strip()
        for raw, label, _dpt in ga_info:
            if needle and needle not in label.lower():
                continue
            checked = raw in self._pinned
            changed, new = imgui.checkbox(f"##pin{raw}", checked)
            if changed:
                if new:
                    self._pinned.add(raw)
                else:
                    self._pinned.discard(raw)
            imgui.same_line()
            imgui.text_colored(color_by_raw[raw], label)

    def _render_chart(
        self, ga_info: list[GaInfo], color_by_raw: dict[int, imgui.ImVec4]
    ) -> None:
        # Time window selector.
        labels = [S.TIMELINE_WINDOW_ALL, "60 s", "300 s"]
        values = [0.0, 60.0, 300.0]
        current = values.index(self._window_s) if self._window_s in values else 0
        imgui.set_next_item_width(px(120.0))
        if imgui.begin_combo(f"{S.TIMELINE_WINDOW}##tl_win", labels[current]):
            for i, lbl in enumerate(labels):
                if imgui.selectable(lbl, i == current)[0]:
                    self._window_s = values[i]
            imgui.end_combo()

        if not self._pinned:
            imgui.text_disabled(S.TIMELINE_NO_PINS)
            return
        dpt_by_raw = {raw: dpt for raw, _l, dpt in ga_info}
        series = self._collect_series(dpt_by_raw)
        if not any(series.values()):
            imgui.text_disabled(S.TIMELINE_NO_DATA)
            return
        self._draw_series(series, color_by_raw, ga_info)

    def _collect_series(
        self, dpt_by_raw: dict[int, str]
    ) -> dict[int, list[tuple[float, float]]]:
        series: dict[int, list[tuple[float, float]]] = {r: [] for r in self._pinned}
        cutoff = time.time() - self._window_s if self._window_s else None
        for t in self._get_telegrams()[-4000:]:
            raw = t.destination_raw
            if raw not in self._pinned:
                continue
            ts = t.timestamp.timestamp()
            if cutoff is not None and ts < cutoff:
                continue
            value = _decode_numeric(t.telegram.payload, dpt_by_raw.get(raw))
            if value is not None:
                series[raw].append((ts, value))
        return series

    def _draw_series(
        self,
        series: dict[int, list[tuple[float, float]]],
        color_by_raw: dict[int, imgui.ImVec4],
        ga_info: list[GaInfo],
    ) -> None:
        pts = [p for s in series.values() for p in s]
        t_min = min(p[0] for p in pts)
        t_max = max(p[0] for p in pts)
        v_min = min(p[1] for p in pts)
        v_max = max(p[1] for p in pts)
        t_span = max(t_max - t_min, 1e-6)
        v_span = max(v_max - v_min, 1e-6)

        origin = imgui.get_cursor_screen_pos()
        avail = imgui.get_content_region_avail()
        pad = px(8.0)
        x0, y0 = origin.x + pad, origin.y + pad
        w = max(avail.x - 2 * pad, px(32.0))
        h = max(
            avail.y - 2 * pad - px(60.0), px(60.0)
        )  # leave room for the legend below
        dl = imgui.get_window_draw_list()
        frame = imgui.get_color_u32(imgui.ImVec4(0.4, 0.4, 0.45, 1.0))
        dl.add_rect(imgui.ImVec2(x0, y0), imgui.ImVec2(x0 + w, y0 + h), frame)

        def sx(t: float) -> float:
            return x0 + (t - t_min) / t_span * w

        def sy(v: float) -> float:
            return y0 + h - (v - v_min) / v_span * h

        labels = {raw: lbl for raw, lbl, _d in ga_info}
        for raw, s in series.items():
            if not s:
                continue
            col = imgui.get_color_u32(color_by_raw[raw])
            for a, b in pairwise(s):
                dl.add_line(
                    imgui.ImVec2(sx(a[0]), sy(a[1])),
                    imgui.ImVec2(sx(b[0]), sy(b[1])),
                    col,
                    px(1.8),
                )
            last = s[-1]
            dl.add_circle_filled(imgui.ImVec2(sx(last[0]), sy(last[1])), px(3.0), col)

        # Reserve the chart area, then a compact legend + min/max readout below it.
        imgui.dummy(imgui.ImVec2(w, h + px(4.0)))
        imgui.text_disabled(f"[{v_min:g} .. {v_max:g}]")
        for raw in series:
            if series[raw]:
                imgui.same_line()
                imgui.text_colored(color_by_raw[raw], f"  {labels.get(raw, raw)}")


def _decode_numeric(payload: Any, dpt: str | None) -> float | None:
    transcoder = transcoder_for(dpt)
    if transcoder is None or payload is None:
        return None
    raw = getattr(payload, "value", None)
    if raw is None:
        return None
    try:
        value = transcoder.from_knx(raw)
    except Exception:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return None
