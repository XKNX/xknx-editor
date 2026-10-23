"""Commissioning cockpit: a site-wide device table with catalog/health status and jump-to-device."""

from __future__ import annotations

from collections.abc import Callable

from imgui_bundle import imgui

from editor_gui.plugins.cockpit.service import CockpitRow, CockpitService, sort_rows
from editor_gui.plugins.cockpit.strings import S
from editor_gui.plugins.health.service import Severity
from editor_gui.widgets.checkbox import checkbox
from editor_gui.widgets.description import (
    description_icon,
    description_icon_width,
    is_secure_device,
    secure_icon,
    secure_icon_width,
    shows_description_icon,
)
from editor_gui.widgets.dpi import px
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.tables import begin_data_table, data_table_headers
from editor_gui.widgets.text_util import text_clipped_tooltip

_ATTENTION_COLOR = imgui.ImVec4(1.0, 0.75, 0.3, 1.0)
_OK_COLOR = imgui.ImVec4(0.45, 0.8, 0.5, 1.0)
_ERROR_COLOR = imgui.ImVec4(0.95, 0.4, 0.4, 1.0)
_INFO_COLOR = imgui.ImVec4(0.6, 0.6, 0.65, 1.0)
_SEVERITY_COLOR = {
    Severity.ERROR: _ERROR_COLOR,
    Severity.WARNING: _ATTENTION_COLOR,
    Severity.INFO: _INFO_COLOR,
}


class CockpitPanel:
    def __init__(
        self,
        service: CockpitService,
        on_select: Callable[[int], None],
        is_open: Callable[[], bool],
    ) -> None:
        self._service = service
        self._on_select = on_select
        self._is_open = is_open
        self._filter = ""
        self._attention_only = False
        self._sort: tuple[int, bool] = (0, False)
        self._sorted_source: list[CockpitRow] | None = None
        self._sorted: list[CockpitRow] = []

    def render(self) -> None:
        if not self._is_open():
            imgui.text_disabled(S.COCKPIT_EMPTY)
            return
        skipped = self._service.skipped_count()
        if skipped:
            imgui.text_colored(
                _ATTENTION_COLOR, S.COCKPIT_SKIPPED.format(count=skipped)
            )
        _, self._attention_only = checkbox(
            S.COCKPIT_ATTENTION_ONLY, self._attention_only
        )
        self._filter = filter_box("##cockpit_filter", S.COCKPIT_SEARCH, self._filter)

        self._render_table(self._filter.lower().strip())

    def _sorted_rows(self) -> list[CockpitRow]:
        specs = imgui.table_get_sort_specs()
        if specs.specs_dirty:
            if specs.specs_count:
                column = specs.get_specs(0)
                self._sort = (
                    column.column_index,
                    column.get_sort_direction() == imgui.SortDirection.descending,
                )
            specs.specs_dirty = False
            self._sorted_source = None
        rows = self._service.rows()
        if rows is not self._sorted_source:
            self._sorted = sort_rows(rows, *self._sort)
            self._sorted_source = rows
        return self._sorted

    def _matches(self, r: CockpitRow, needle: str) -> bool:
        if self._attention_only and not r.needs_attention:
            return False
        if not needle:
            return True
        return (
            needle in r.name.lower()
            or needle in r.individual_address.lower()
            or needle in r.product_name.lower()
            or needle in r.order_number.lower()
            or needle in r.commissioning.lower()
        )

    def _render_table(self, needle: str) -> None:
        flags = (
            imgui.TableFlags_.row_bg
            | imgui.TableFlags_.borders_inner_h
            | imgui.TableFlags_.scroll_y
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.sortable
            | imgui.TableFlags_.reorderable
        )
        avail = imgui.get_content_region_avail()
        if not begin_data_table("##cockpit", 5, flags, imgui.ImVec2(avail.x, avail.y)):
            return
        imgui.table_setup_scroll_freeze(0, 1)
        imgui.table_setup_column(
            S.COCKPIT_COL_ADDRESS,
            imgui.TableColumnFlags_.width_fixed | imgui.TableColumnFlags_.default_sort,
            px(54.0),
        )
        imgui.table_setup_column(
            S.COCKPIT_COL_NAME, imgui.TableColumnFlags_.width_stretch, 0.403
        )
        imgui.table_setup_column(
            S.COCKPIT_COL_PRODUCT, imgui.TableColumnFlags_.width_stretch, 0.403
        )
        imgui.table_setup_column(
            S.COCKPIT_COL_LOADED, imgui.TableColumnFlags_.width_fixed, px(66.0)
        )
        imgui.table_setup_column(
            S.COCKPIT_COL_STATUS, imgui.TableColumnFlags_.width_stretch, 0.194
        )
        data_table_headers()
        rows = [r for r in self._sorted_rows() if self._matches(r, needle)]
        for i, r in enumerate(rows):
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            if imgui.selectable(
                f"{r.individual_address or '-'}##cockpit{i}",
                False,
                imgui.SelectableFlags_.span_all_columns,
            )[0]:
                self._on_select(r.node_id)
            imgui.table_set_column_index(1)
            secure = is_secure_device(r.node_id)
            long_description = shows_description_icon(r.description)
            if secure or long_description:
                # The KNX Data Secure shield and the description's icon at the end of the cell, the
                # name (and a short description) clipped before them.
                spacing = imgui.get_style().item_inner_spacing.x
                icons_w = (secure_icon_width() if secure else 0.0) + (
                    description_icon_width() if long_description else 0.0
                )
                if secure and long_description:
                    icons_w += spacing
                start = imgui.get_cursor_screen_pos().x
                end = start + imgui.get_content_region_avail().x
                icon_x = max(start, end - icons_w)
                room = max(0.0, icon_x - spacing - start)
                text_clipped_tooltip(r.name, width=room)
                used = imgui.calc_text_size(r.name).x + imgui.get_style().item_spacing.x
                if r.description and not long_description and used < room:
                    # A short description inline after the name, dimmed.
                    imgui.same_line()
                    text_clipped_tooltip(
                        r.description, disabled=True, width=room - used
                    )
                if secure:
                    secure_icon(x=icon_x)
                    icon_x += secure_icon_width() + spacing
                if long_description:
                    description_icon(r.description, x=icon_x)
            elif r.description:
                # A short description inline after the name.
                text_clipped_tooltip(r.name)
                imgui.same_line()
                imgui.text_disabled(r.description)
            else:
                text_clipped_tooltip(r.name)
            imgui.table_set_column_index(2)
            text_clipped_tooltip(r.product or "-", disabled=True)
            imgui.table_set_column_index(3)
            text_clipped_tooltip(r.commissioning or "-", disabled=True)
            if r.commissioning_tooltip and imgui.is_item_hovered():
                imgui.set_tooltip(r.commissioning_tooltip)
            imgui.table_set_column_index(4)
            if r.needs_attention:
                sev = r.top_severity
                color = _SEVERITY_COLOR[sev] if sev is not None else _ATTENTION_COLOR
                imgui.push_style_color(imgui.Col_.text, color)
                text_clipped_tooltip(r.issue_summary)
                imgui.pop_style_color()
                if imgui.is_item_hovered():
                    imgui.set_tooltip(r.issue_tooltip)
            else:
                imgui.text_colored(_OK_COLOR, S.COCKPIT_OK)
        imgui.end_table()
