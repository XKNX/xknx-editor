"""Read-only "Generic Plug-in" tab: the settings the Siemens Generic Plug-in stored for a device.

Shows each parameter block of the plug-in as a collapsing section with the setting's name, its
value (enumeration text and symbolic value; "Percent" numbers scaled, with the raw value) and
whether the memory image of the last download holds that value. Settings of extension modules
that are not fitted can be shown on request. Another device of the same application can be put
beside it: an extra column with its values, differences marked. The views come from
``editor_gui.plugin_blob_readers.siemens_generic`` through callbacks the project plugin injects;
nothing here edits or downloads.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.device import device_label
from editor_gui.plugin_blob_readers.siemens_generic import (
    ComparePage,
    CompareRow,
    GenericView,
    MemoryCheck,
    Page,
    Row,
    ViewComparison,
    compare_views,
)
from editor_gui.plugins.project.strings import S
from editor_gui.theme import status_color
from editor_gui.widgets.combo import begin_combo
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.tables import begin_data_table, data_table_headers

if TYPE_CHECKING:
    from editor_gui.device import Device

ViewProvider = Callable[..., "GenericView | None"]  # (device, show_unfitted=False)
CandidateProvider = Callable[["Device"], "list[Device]"]


def page_title(page: Page | ComparePage) -> str:
    """The heading of ``page``: its block text, else "General"; marked when it belongs to a
    module that is not fitted."""
    title = page.title or S.GENERIC_PLUGIN_GENERAL
    if not page.fitted:
        return S.GENERIC_PLUGIN_UNFITTED_PAGE.format(title=title)
    return title


def memory_text(row: Row) -> str:
    """The Memory Image column of ``row``."""
    if not row.fitted:
        return S.GENERIC_PLUGIN_NOT_FITTED
    if row.memory is MemoryCheck.MATCH:
        return S.GENERIC_PLUGIN_MATCH
    if row.memory is MemoryCheck.MISMATCH:
        held = row.memory_value_text or row.memory_value
        if row.memory_value_text and row.memory_value_text != row.memory_value:
            if row.scaled:
                held = S.GENERIC_PLUGIN_SCALED.format(
                    value=row.memory_value_text, raw=row.memory_value
                )
            else:
                held = f"{row.memory_value_text} ({row.memory_value})"
        return S.GENERIC_PLUGIN_MISMATCH.format(value=held)
    if row.memory is MemoryCheck.NOT_IN_MEMORY:
        return S.GENERIC_PLUGIN_NOT_IN_MEMORY
    return S.GENERIC_PLUGIN_UNCHECKED


def value_text(row: Row | None) -> str:
    """The Value column of ``row``: the meaning, with the symbolic value when it differs; a
    scaled "Percent" number with its raw value; "Not shown" when the device lacks it."""
    if row is None:
        return S.GENERIC_PLUGIN_NOT_SHOWN
    if row.scaled:
        return S.GENERIC_PLUGIN_SCALED.format(value=row.value_text, raw=row.value)
    if row.value_text and row.value_text != row.value:
        return f"{row.value_text} ({row.value})"
    return row.value


def summary_lines(view: GenericView) -> list[tuple[str, str]]:
    """The (status kind, text) lines above the pages."""
    lines: list[tuple[str, str]] = []
    if not view.application_matches:
        lines.append(
            (
                "warning",
                S.GENERIC_PLUGIN_OTHER_APPLICATION.format(
                    stored=view.stored_application_id
                ),
            )
        )
    if not view.has_image:
        lines.append(("info", S.GENERIC_PLUGIN_NO_IMAGE))
    elif not view.image_layout_known:
        lines.append(("warning", S.GENERIC_PLUGIN_LAYOUT_UNKNOWN))
    else:
        mismatch = view.count(MemoryCheck.MISMATCH)
        lines.append(
            (
                "warning" if mismatch else "success",
                S.GENERIC_PLUGIN_SUMMARY.format(
                    match=view.count(MemoryCheck.MATCH),
                    mismatch=mismatch,
                    unchecked=view.count(MemoryCheck.UNCHECKED),
                ),
            )
        )
    if view.show_unfitted and view.unfitted_count:
        lines.append(
            ("info", S.GENERIC_PLUGIN_UNFITTED_COUNT.format(count=view.unfitted_count))
        )
    if view.last_download:
        lines.append(
            (
                "info",
                S.GENERIC_PLUGIN_LAST_DOWNLOAD.format(
                    date=view.last_download.replace("T", " ")[:19]
                ),
            )
        )
    return lines


def _row_hit(row: Row | None, needle: str) -> bool:
    return row is not None and (
        needle in row.label.lower()
        or needle in row.value_text.lower()
        or needle in row.value.lower()
    )


def filter_rows(page: Page, needle: str, only_mismatches: bool) -> list[Row]:
    """The rows of ``page`` the filter keeps (all of them when the page title matches)."""
    rows = page.rows
    if only_mismatches:
        rows = [r for r in rows if r.memory is MemoryCheck.MISMATCH]
    if not needle or needle in page_title(page).lower():
        return rows
    return [r for r in rows if not r.separator and _row_hit(r, needle)]


def filter_compare_rows(
    page: ComparePage, needle: str, only_mismatches: bool, only_differences: bool
) -> list[CompareRow]:
    """The side-by-side rows of ``page`` the filters keep."""
    rows = page.rows
    if only_differences:
        rows = [r for r in rows if r.differs]
    if only_mismatches:
        rows = [
            r
            for r in rows
            if any(
                x is not None and x.memory is MemoryCheck.MISMATCH
                for x in (r.first, r.second)
            )
        ]
    if not needle or needle in page_title(page).lower():
        return rows
    return [r for r in rows if _row_hit(r.first, needle) or _row_hit(r.second, needle)]


@dataclass
class GenericPluginPanel:
    """Panel body of the read-only Generic Plug-in tab."""

    _view_for: ViewProvider
    _candidates_for: CandidateProvider | None = None
    _filter: str = ""
    _only_mismatches: bool = False
    _only_differences: bool = False
    _show_unfitted: bool = False
    # The device each device is compared with (by individual address of the shown device).
    _compare: dict[str, int] = field(default_factory=dict[str, int])
    _closed: dict[str, set[str]] = field(default_factory=dict[str, "set[str]"])

    def render(self, device: Device) -> None:
        view = self._view_for(device, show_unfitted=self._show_unfitted)
        if view is None:
            imgui.text_disabled(S.GENERIC_PLUGIN_NONE)
            return
        imgui.text_wrapped(S.GENERIC_PLUGIN_NOTE)
        for kind, text in summary_lines(view):
            imgui.text_colored(status_color(kind), text)
        other = self._render_compare_picker(device)
        other_view = (
            self._view_for(other, show_unfitted=self._show_unfitted)
            if other is not None
            else None
        )
        comparison = (
            compare_views(view, other_view)
            if other is not None and other_view is not None
            else None
        )
        if other is not None and comparison is not None:
            differences = comparison.differences
            imgui.text_colored(
                status_color("warning" if differences else "success"),
                S.GENERIC_PLUGIN_COMPARE_DIFFERENCES.format(
                    device=other.individual_address, count=differences
                ),
            )
        self._filter = filter_box(
            "##generic_plugin_filter", S.CONFIGURE_PARAM_FILTER_HINT, self._filter
        )
        _, self._only_mismatches = imgui.checkbox(
            S.GENERIC_PLUGIN_ONLY_MISMATCHES, self._only_mismatches
        )
        _, self._show_unfitted = imgui.checkbox(
            S.GENERIC_PLUGIN_SHOW_UNFITTED, self._show_unfitted
        )
        if other is not None and comparison is not None:
            _, self._only_differences = imgui.checkbox(
                S.GENERIC_PLUGIN_ONLY_DIFFERENCES, self._only_differences
            )
            self._render_comparison(device, other, comparison)
        else:
            self._render_pages(device, view)

    def _render_compare_picker(self, device: Device) -> Device | None:
        """The "Compare With" drop-down (only when another device of the same application
        exists); the chosen device, or None."""
        if self._candidates_for is None:
            return None
        candidates = self._candidates_for(device)
        if not candidates:
            return None
        key = device.individual_address
        chosen = next(
            (d for d in candidates if d.node_id == self._compare.get(key)), None
        )
        imgui.align_text_to_frame_padding()
        imgui.text(S.GENERIC_PLUGIN_COMPARE_WITH)
        imgui.same_line()
        imgui.set_next_item_width(-1)
        label = (
            device_label(chosen)
            if chosen is not None
            else S.GENERIC_PLUGIN_COMPARE_NONE
        )
        if begin_combo("##generic_plugin_compare", label):
            if imgui.selectable(
                f"{S.GENERIC_PLUGIN_COMPARE_NONE}##generic_compare_none", chosen is None
            )[0]:
                self._compare.pop(key, None)
                chosen = None
            for d in candidates:
                if imgui.selectable(
                    f"{device_label(d)}##generic_compare_{d.node_id}",
                    chosen is not None and d.node_id == chosen.node_id,
                )[0]:
                    self._compare[key] = d.node_id
                    chosen = d
            imgui.end_combo()
        return chosen

    def _render_pages(self, device: Device, view: GenericView) -> None:
        needle = self._filter.lower().strip()
        filtering = bool(needle) or self._only_mismatches
        closed = self._closed.setdefault(device.individual_address, set())
        shown = 0
        for index, page in enumerate(view.pages):
            rows = filter_rows(page, needle, self._only_mismatches)
            if not rows:
                continue
            shown += len(rows)
            if self._header(page, index, len(rows), closed, filtering):
                self._render_table(rows)
            imgui.pop_id()
        if shown == 0:
            imgui.text_disabled(S.PLUGIN_PARAMS_FILTER_EMPTY)

    def _render_comparison(
        self, device: Device, other: Device, comparison: ViewComparison
    ) -> None:
        needle = self._filter.lower().strip()
        filtering = bool(needle) or self._only_mismatches or self._only_differences
        closed = self._closed.setdefault(device.individual_address, set())
        shown = 0
        for index, page in enumerate(comparison.pages):
            rows = filter_compare_rows(
                page, needle, self._only_mismatches, self._only_differences
            )
            if not rows:
                continue
            shown += len(rows)
            if self._header(page, index, len(rows), closed, filtering):
                self._render_compare_table(rows, other)
            imgui.pop_id()
        if shown == 0:
            imgui.text_disabled(S.PLUGIN_PARAMS_FILTER_EMPTY)

    def _header(
        self,
        page: Page | ComparePage,
        index: int,
        count: int,
        closed: set[str],
        filtering: bool,
    ) -> bool:
        """Push the page's id scope and draw its collapsing header; whether it is open. The
        caller pops the id scope."""
        key = (page.id or str(index)) + ("" if page.fitted else "~")
        imgui.push_id(f"generic_page_{index}")
        if filtering:
            imgui.set_next_item_open(True)
        elif key not in closed:
            imgui.set_next_item_open(page.fitted, imgui.Cond_.appearing)
        if imgui.collapsing_header(f"{page_title(page)}  ({count})"):
            if not filtering:
                closed.discard(key)
            return True
        if not filtering:
            closed.add(key)
        return False

    def _render_table(self, rows: list[Row]) -> None:
        flags = (
            imgui.TableFlags_.borders
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
        )
        if not begin_data_table("##generic_plugin_rows", 3, flags):
            return
        imgui.table_setup_column(
            S.PLUGIN_PARAMS_COL_NAME, imgui.TableColumnFlags_.width_stretch, 0.5
        )
        imgui.table_setup_column(
            S.PLUGIN_PARAMS_COL_VALUE, imgui.TableColumnFlags_.width_stretch, 0.3
        )
        imgui.table_setup_column(
            S.GENERIC_PLUGIN_COL_MEMORY, imgui.TableColumnFlags_.width_stretch, 0.2
        )
        data_table_headers()
        for row in rows:
            imgui.table_next_row()
            imgui.table_next_column()
            if row.separator:
                imgui.text_disabled(row.label)
                continue
            _text(row.label, row.fitted)
            imgui.table_next_column()
            _text(value_text(row), row.stored and row.fitted)
            imgui.table_next_column()
            _memory_cell(row)
        imgui.end_table()

    def _render_compare_table(self, rows: list[CompareRow], other: Device) -> None:
        flags = (
            imgui.TableFlags_.borders
            | imgui.TableFlags_.row_bg
            | imgui.TableFlags_.resizable
        )
        if not begin_data_table("##generic_plugin_compare_rows", 4, flags):
            return
        imgui.table_setup_column(
            S.PLUGIN_PARAMS_COL_NAME, imgui.TableColumnFlags_.width_stretch, 0.4
        )
        imgui.table_setup_column(
            S.PLUGIN_PARAMS_COL_VALUE, imgui.TableColumnFlags_.width_stretch, 0.22
        )
        imgui.table_setup_column(
            S.GENERIC_PLUGIN_COL_OTHER.format(device=other.individual_address),
            imgui.TableColumnFlags_.width_stretch,
            0.22,
        )
        imgui.table_setup_column(
            S.GENERIC_PLUGIN_COL_MEMORY, imgui.TableColumnFlags_.width_stretch, 0.16
        )
        data_table_headers()
        for entry in rows:
            row = entry.row
            imgui.table_next_row()
            imgui.table_next_column()
            _text(row.label, row.fitted)
            for side in (entry.first, entry.second):
                imgui.table_next_column()
                if entry.differs:
                    imgui.text_colored(status_color("warning"), value_text(side))
                else:
                    _text(
                        value_text(side),
                        side is not None and side.stored and side.fitted,
                    )
            imgui.table_next_column()
            if entry.first is not None:
                _memory_cell(entry.first)
            else:
                imgui.text_disabled(S.GENERIC_PLUGIN_NOT_SHOWN)
        imgui.end_table()


def _text(text: str, bright: bool) -> None:
    if bright:
        imgui.text_wrapped(text)
    else:
        imgui.text_disabled(text)


def _memory_cell(row: Row) -> None:
    text = memory_text(row)
    if row.memory is MemoryCheck.MISMATCH:
        imgui.text_colored(status_color("warning"), text)
    elif row.memory is MemoryCheck.MATCH:
        imgui.text_colored(status_color("success"), text)
    else:
        imgui.text_disabled(text)
