"""Read-only "Generic Plug-in" tab: the settings the Siemens Generic Plug-in stored for a device.

Shows each parameter block of the plug-in as a collapsing section with the setting's name, its
value (enumeration text and symbolic value) and whether the memory image of the last download
holds that value. The view comes from ``editor_gui.plugin_blob_readers.siemens_generic`` through
a callback the project plugin injects; nothing here edits or downloads.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from imgui_bundle import imgui

from editor_gui.plugin_blob_readers.siemens_generic import (
    GenericView,
    MemoryCheck,
    Page,
    Row,
)
from editor_gui.plugins.project.strings import S
from editor_gui.theme import status_color
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.tables import begin_data_table, data_table_headers

if TYPE_CHECKING:
    from editor_gui.device import Device

ViewProvider = Callable[["Device"], "GenericView | None"]


def page_title(page: Page) -> str:
    """The heading of ``page``: its block text, else "General"."""
    return page.title or S.GENERIC_PLUGIN_GENERAL


def memory_text(row: Row) -> str:
    """The Memory Image column of ``row``."""
    if row.memory is MemoryCheck.MATCH:
        return S.GENERIC_PLUGIN_MATCH
    if row.memory is MemoryCheck.MISMATCH:
        held = row.memory_value_text or row.memory_value
        if row.memory_value_text and row.memory_value_text != row.memory_value:
            held = f"{row.memory_value_text} ({row.memory_value})"
        return S.GENERIC_PLUGIN_MISMATCH.format(value=held)
    if row.memory is MemoryCheck.NOT_IN_MEMORY:
        return S.GENERIC_PLUGIN_NOT_IN_MEMORY
    return S.GENERIC_PLUGIN_UNCHECKED


def value_text(row: Row) -> str:
    """The Value column of ``row``: the meaning, with the symbolic value when it differs."""
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


def filter_rows(page: Page, needle: str, only_mismatches: bool) -> list[Row]:
    """The rows of ``page`` the filter keeps (all of them when the page title matches)."""
    rows = page.rows
    if only_mismatches:
        rows = [r for r in rows if r.memory is MemoryCheck.MISMATCH]
    if not needle or needle in page_title(page).lower():
        return rows
    return [
        r
        for r in rows
        if not r.separator
        and (
            needle in r.label.lower()
            or needle in r.value_text.lower()
            or needle in r.value.lower()
        )
    ]


@dataclass
class GenericPluginPanel:
    """Panel body of the read-only Generic Plug-in tab."""

    _view_for: ViewProvider
    _filter: str = ""
    _only_mismatches: bool = False
    _closed: dict[str, set[str]] = field(default_factory=dict[str, "set[str]"])

    def render(self, device: Device) -> None:
        view = self._view_for(device)
        if view is None:
            imgui.text_disabled(S.GENERIC_PLUGIN_NONE)
            return
        imgui.text_wrapped(S.GENERIC_PLUGIN_NOTE)
        for kind, text in summary_lines(view):
            imgui.text_colored(status_color(kind), text)
        self._filter = filter_box(
            "##generic_plugin_filter", S.CONFIGURE_PARAM_FILTER_HINT, self._filter
        )
        _, self._only_mismatches = imgui.checkbox(
            S.GENERIC_PLUGIN_ONLY_MISMATCHES, self._only_mismatches
        )
        needle = self._filter.lower().strip()
        filtering = bool(needle) or self._only_mismatches
        closed = self._closed.setdefault(device.individual_address, set())
        shown = 0
        for index, page in enumerate(view.pages):
            rows = filter_rows(page, needle, self._only_mismatches)
            if not rows:
                continue
            shown += len(rows)
            key = page.id or str(index)
            imgui.push_id(f"generic_page_{index}")
            if filtering:
                imgui.set_next_item_open(True)
            elif key not in closed:
                imgui.set_next_item_open(True, imgui.Cond_.appearing)
            title = f"{page_title(page)}  ({len(rows)})"
            if imgui.collapsing_header(title):
                if not filtering:
                    closed.discard(key)
                self._render_table(rows)
            elif not filtering:
                closed.add(key)
            imgui.pop_id()
        if shown == 0:
            imgui.text_disabled(S.PLUGIN_PARAMS_FILTER_EMPTY)

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
            imgui.text_wrapped(row.label)
            imgui.table_next_column()
            if row.stored:
                imgui.text_wrapped(value_text(row))
            else:
                imgui.text_disabled(value_text(row))
            imgui.table_next_column()
            text = memory_text(row)
            if row.memory is MemoryCheck.MISMATCH:
                imgui.text_colored(status_color("warning"), text)
            elif row.memory is MemoryCheck.MATCH:
                imgui.text_colored(status_color("success"), text)
            else:
                imgui.text_disabled(text)
        imgui.end_table()
