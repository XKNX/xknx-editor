"""Group monitor panel: a table of the project's group addresses with their latest bus value,
plus a command bar to write/read the selected group address (values decoded via the GA's DPT)."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from imgui_bundle import imgui

from editor_gui.dpt import transcoder_for
from editor_gui.plugins.monitor.strings import S
from editor_gui.widgets.dpi import px
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.text_util import text_clipped_tooltip

if TYPE_CHECKING:
    from editor_gui.plugins.monitor.service import MonitorService
    from editor_gui.plugins.project.service import _GroupAddress


class MonitorPanel:
    def __init__(
        self,
        service: "MonitorService",
        get_group_addresses: "Callable[[], list[_GroupAddress]]",
        is_connected: Callable[[], bool],
    ) -> None:
        self._service = service
        self._get_group_addresses = get_group_addresses
        self._is_connected = is_connected
        self._selected: str | None = None
        self._write_value = ""
        self._filter = ""
        self._bus_filter = ""

    def render(self) -> None:
        if imgui.begin_tab_bar("##monitor_tabs"):
            if imgui.begin_tab_item(S.MONITOR_TAB_GROUP_OBJECTS)[0]:
                self._render_group_objects()
                imgui.end_tab_item()
            if imgui.begin_tab_item(S.MONITOR_TAB_BUS)[0]:
                self._render_bus_monitor()
                imgui.end_tab_item()
            imgui.end_tab_bar()

    def _render_group_objects(self) -> None:
        gas = self._get_group_addresses()
        if not gas:
            imgui.text_disabled(S.MONITOR_NO_GAS)
            return

        self._render_command_bar()
        self._filter = filter_box("##mon_filter", S.MONITOR_FILTER_HINT, self._filter)
        needle = self._filter.lower()
        shown = [
            ga
            for ga in gas
            if not needle or needle in ga.address.lower() or needle in ga.name.lower()
        ]
        imgui.separator()
        self._render_table(shown)

    def _render_bus_monitor(self) -> None:
        """Live scrolling log of every group telegram on the bus (incl. addresses not in the
        project), similar to a Group/Bus Monitor."""
        by_addr = {ga.address: ga for ga in self._get_group_addresses()}
        if not self._is_connected():
            imgui.text_disabled(S.MONITOR_DISCONNECTED)
        if imgui.button(S.MONITOR_CLEAR):
            self._service.clear()
        imgui.same_line()
        imgui.text_disabled(
            S.MONITOR_BUS_LOAD.format(rate=f"{self._service.bus_load():.1f}")
        )
        records = self._service.telegrams()
        self._bus_filter = filter_box(
            "##bus_filter", S.MONITOR_FILTER_HINT, self._bus_filter
        )
        needle = self._bus_filter.lower().strip()
        if needle:
            records = [
                r
                for r in records
                if needle in r.source.lower()
                or needle in r.destination.lower()
                or needle in r.service.lower()
                or needle
                in (
                    by_addr[r.destination].name.lower()
                    if r.destination in by_addr
                    else ""
                )
            ]
        imgui.text_disabled(S.MONITOR_TELEGRAM_COUNT.format(count=len(records)))
        flags = (
            imgui.TableFlags_.borders_inner
            | imgui.TableFlags_.sizing_stretch_prop
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.scroll_y
        )
        if not imgui.begin_table("##busmon", 6, flags):
            return
        imgui.table_setup_column("Time", imgui.TableColumnFlags_.width_stretch, 0.12)
        imgui.table_setup_column("Source", imgui.TableColumnFlags_.width_stretch, 0.12)
        imgui.table_setup_column(
            "Destination", imgui.TableColumnFlags_.width_stretch, 0.14
        )
        imgui.table_setup_column("Name", imgui.TableColumnFlags_.width_stretch, 0.3)
        imgui.table_setup_column("Service", imgui.TableColumnFlags_.width_stretch, 0.12)
        imgui.table_setup_column("Value", imgui.TableColumnFlags_.width_stretch, 0.2)
        imgui.table_headers_row()
        # Only build/format the rows imgui actually shows: ListClipper skips off-screen rows so the
        # per-frame cost is O(visible), not O(2000). (Row timestamps are cached on the record.)
        rows = list(reversed(records))  # newest first
        clipper = imgui.ListClipper()
        clipper.begin(len(rows))
        while clipper.step():
            for i in range(clipper.display_start, clipper.display_end):
                rec = rows[i]
                ga = by_addr.get(rec.destination)
                dpt = ga.datapoint_type if ga else None
                imgui.table_next_row()
                imgui.table_set_column_index(0)
                imgui.text(rec.time_str)
                imgui.table_set_column_index(1)
                imgui.text_disabled(rec.source)
                imgui.table_set_column_index(2)
                imgui.text(rec.destination)
                imgui.table_set_column_index(3)
                text_clipped_tooltip(ga.name if ga else "", disabled=True)
                imgui.table_set_column_index(4)
                imgui.text(rec.service)
                imgui.table_set_column_index(5)
                text_clipped_tooltip(
                    _decode(rec.payload, dpt) if rec.payload is not None else ""
                )
        imgui.end_table()

    def _render_command_bar(self) -> None:
        connected = self._is_connected()
        if not connected:
            imgui.text_disabled(S.MONITOR_DISCONNECTED)
        imgui.begin_disabled(not connected or self._selected is None)
        # The selected group address (set by clicking a table row), then the value to send.
        imgui.text(self._selected or "-")
        imgui.same_line()
        imgui.text_disabled(S.MONITOR_VALUE_LABEL)
        imgui.same_line()
        imgui.set_next_item_width(px(160.0))
        submitted, self._write_value = imgui.input_text_with_hint(
            "##mon_value",
            S.MONITOR_VALUE_HINT,
            self._write_value,
            imgui.InputTextFlags_.enter_returns_true,
        )
        imgui.same_line()
        write = imgui.button(S.MONITOR_WRITE) or submitted
        imgui.same_line()
        read = imgui.button(S.MONITOR_READ)
        imgui.end_disabled()

        if self._selected is None or not connected:
            return
        dpt = self._dpt_for(self._selected)
        if write:
            self._service.send_write(self._selected, dpt, self._write_value)
        if read:
            self._service.send_read(self._selected)

    def _render_table(self, gas: "list[_GroupAddress]") -> None:
        flags = (
            imgui.TableFlags_.borders_inner
            | imgui.TableFlags_.sizing_stretch_prop
            | imgui.TableFlags_.resizable
            | imgui.TableFlags_.scroll_y
        )
        if not imgui.begin_table("##monitor", 5, flags):
            return
        imgui.table_setup_column("Address", imgui.TableColumnFlags_.width_stretch, 0.16)
        imgui.table_setup_column("Name", imgui.TableColumnFlags_.width_stretch, 0.34)
        imgui.table_setup_column("DPT", imgui.TableColumnFlags_.width_stretch, 0.14)
        imgui.table_setup_column("Value", imgui.TableColumnFlags_.width_stretch, 0.26)
        imgui.table_setup_column("Time", imgui.TableColumnFlags_.width_stretch, 0.1)
        imgui.table_headers_row()

        for ga in gas:
            imgui.table_next_row()
            imgui.table_set_column_index(0)
            if imgui.selectable(
                f"{ga.address}##mon{ga.id}",
                self._selected == ga.address,
                imgui.SelectableFlags_.span_all_columns,
            )[0]:
                self._selected = ga.address
            imgui.table_set_column_index(1)
            text_clipped_tooltip(ga.name)
            imgui.table_set_column_index(2)
            imgui.text_disabled(ga.datapoint_type or "")
            latest = self._service.latest(ga.address)
            imgui.table_set_column_index(3)
            text_clipped_tooltip(
                _decode(latest.payload, ga.datapoint_type) if latest else ""
            )
            imgui.table_set_column_index(4)
            imgui.text_disabled(latest.time_str if latest else "")

        imgui.end_table()

    def _dpt_for(self, address: str) -> str | None:
        for ga in self._get_group_addresses():
            if ga.address == address:
                return ga.datapoint_type
        return None


def _decode(payload: Any, dpt: str | None) -> str:
    transcoder = transcoder_for(dpt)
    if transcoder is not None:
        try:
            return str(transcoder.from_knx(payload))
        except Exception:
            pass
    value = getattr(payload, "value", None)
    if isinstance(value, tuple):
        return " ".join(f"{b:02x}" for b in value)
    return str(value) if value is not None else "?"
