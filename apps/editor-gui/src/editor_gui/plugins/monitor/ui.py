"""Monitor panel: Group Monitor, Bus Monitor and Bus Activity recordings, and a table of the
project's group addresses with their latest bus value. The Group Monitor and the group objects
have a command bar to write/read a group address (values decoded via the GA's DPT)."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from imgui_bundle import imgui

from editor_gui.dpt import transcoder_for
from editor_gui.plugins.monitor.formatting import ProjectIndex, dpt_text
from editor_gui.plugins.monitor.service import MonitorKind
from editor_gui.plugins.monitor.strings import S
from editor_gui.plugins.monitor.table import TelegramView
from editor_gui.widgets.dpi import px
from editor_gui.widgets.dpt_value import DptValueInput
from editor_gui.widgets.filter_box import filter_box
from editor_gui.widgets.text_util import text_clipped_tooltip

if TYPE_CHECKING:
    from editor_gui.plugins.connection.service import ConnectionService
    from editor_gui.plugins.monitor.records import MonitorRecord
    from editor_gui.plugins.monitor.service import MonitorService
    from editor_gui.plugins.project.service import _GroupAddress


_ERROR_COLOR = imgui.ImVec4(0.95, 0.45, 0.4, 1.0)


class MonitorPanel:
    def __init__(
        self,
        service: MonitorService,
        connection: ConnectionService,
        get_group_addresses: Callable[[], list[_GroupAddress]],
        get_index: Callable[[], ProjectIndex],
        notify: Callable[[str], None] | None = None,
        on_select_device: Callable[[int], None] | None = None,
    ) -> None:
        self._service = service
        self._connection = connection
        self._get_group_addresses = get_group_addresses
        self._get_index = get_index
        self._views = {
            kind: TelegramView(kind, service, connection, notify, on_select_device)
            for kind in MonitorKind
        }
        # Group Objects tab.
        self._selected: str | None = None
        self._write_value = DptValueInput("mon_value")
        self._filter = ""
        # Group Monitor command bar.
        self._cmd_address = ""
        self._cmd_value = DptValueInput("mon_cmd_value")
        self._cmd_focused: MonitorRecord | None = None

    def _is_connected(self) -> bool:
        return self._connection.xknx is not None

    @property
    def monitor_extended(self) -> bool:
        return self._views[MonitorKind.GROUP].extended

    def set_monitor_extended(self, value: bool) -> None:
        for view in self._views.values():
            view.extended = value

    def set_layout_toggler(self, toggle: Callable[[], None]) -> None:
        """Wire the monitors' Compact/Extend toggle button (next to the filter)."""
        for view in self._views.values():
            view.set_layout_toggler(toggle)

    def toggle_monitor_extended(self) -> None:
        self.set_monitor_extended(not self.monitor_extended)

    def render(self) -> None:
        if not imgui.begin_tab_bar("##monitor_tabs"):
            return
        index = self._get_index()
        tabs = (
            (S.MONITOR_TAB_GROUP, MonitorKind.GROUP, "group"),
            (S.MONITOR_TAB_BUS, MonitorKind.BUS, "bus"),
            (S.MONITOR_TAB_ACTIVITY, MonitorKind.ACTIVITY, "activity"),
        )
        for label, kind, tab_id in tabs:
            if imgui.begin_tab_item(f"{label}###mon_tab_{tab_id}")[0]:
                bar = (
                    (lambda: self._render_group_command_bar(index))
                    if kind is MonitorKind.GROUP
                    else None
                )
                self._views[kind].render(index, bar)
                imgui.end_tab_item()
        if imgui.begin_tab_item(f"{S.MONITOR_TAB_GROUP_OBJECTS}###mon_tab_objects")[0]:
            self._render_group_objects()
            imgui.end_tab_item()
        imgui.end_tab_bar()

    # --- Group Monitor command bar -----------------------------------------

    def _render_group_command_bar(self, index: ProjectIndex) -> None:
        self._prefill_from_selection(index)
        connected = self._is_connected()
        raw = index.group_address_raw(self._cmd_address) if self._cmd_address else None
        entry = index.group_addresses.get(raw) if raw is not None else None
        imgui.align_text_to_frame_padding()
        imgui.text_disabled(S.MONITOR_ADDRESS_LABEL)
        imgui.same_line()
        imgui.set_next_item_width(px(90.0))
        _, self._cmd_address = imgui.input_text_with_hint(
            "##mon_cmd_address", S.MONITOR_ADDRESS_HINT, self._cmd_address
        )
        if entry is not None:
            imgui.same_line()
            imgui.text_disabled(dpt_text(entry.dpt) or entry.name)
            if imgui.is_item_hovered():
                imgui.set_tooltip(entry.name)
        imgui.same_line()
        imgui.text_disabled(S.MONITOR_VALUE_LABEL)
        imgui.same_line()
        token = entry.dpt if entry is not None else None
        submitted = False
        if token:
            submitted = self._cmd_value.render(token, index.formats, inline=True)
        else:
            imgui.text_disabled("-")
        imgui.same_line()
        can_write = (
            connected
            and entry is not None
            and self._cmd_value.can_write(token, index.formats)
        )
        imgui.begin_disabled(not can_write)
        write = imgui.button(f"{S.MONITOR_WRITE}##mon_cmd_write") or (
            submitted and can_write
        )
        imgui.end_disabled()
        if not can_write and connected:
            imgui.set_item_tooltip(S.MONITOR_UNKNOWN_ADDRESS)
        imgui.same_line()
        imgui.begin_disabled(not connected or raw is None)
        read = imgui.button(f"{S.MONITOR_READ}##mon_cmd_read")
        imgui.end_disabled()
        if not connected:
            imgui.same_line()
            imgui.text_disabled(S.MONITOR_DISCONNECTED)
        if self._cmd_value.error:
            imgui.text_colored(_ERROR_COLOR, self._cmd_value.error)
        if raw is None or not connected:
            return
        address = entry.address if entry is not None else self._cmd_address.strip()
        if write and entry is not None:
            payload = self._cmd_value.payload()
            if payload is not None:
                self._service.send_payload(address, payload)
        if read:
            self._service.send_read(address)

    def _prefill_from_selection(self, index: ProjectIndex) -> None:
        focused = self._views[MonitorKind.GROUP].focused
        if focused is None or focused is self._cmd_focused:
            return
        self._cmd_focused = focused
        frame = focused.frame
        if frame is None or not frame.group_value or frame.destination is None:
            return
        self._cmd_address = index.group_address_text(frame.destination)
        payload = frame.value_payload
        entry = index.group_addresses.get(frame.destination)
        if payload is None or entry is None or not entry.dpt:
            return
        self._cmd_value.set_payload(entry.dpt, index.formats, payload)

    # --- Group Objects tab --------------------------------------------------

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

    def _render_command_bar(self) -> None:
        connected = self._is_connected()
        if not connected:
            imgui.text_disabled(S.MONITOR_DISCONNECTED)
        formats = self._get_index().formats
        token = self._dpt_for(self._selected) if self._selected is not None else None
        imgui.begin_disabled(not connected or self._selected is None)
        # The selected group address (set by clicking a table row), then the value to send.
        imgui.align_text_to_frame_padding()
        imgui.text(self._selected or "-")
        imgui.same_line()
        imgui.text_disabled(S.MONITOR_VALUE_LABEL)
        imgui.same_line()
        submitted = False
        if token:
            submitted = self._write_value.render(token, formats, inline=True)
        else:
            imgui.text_disabled("-")
        imgui.same_line()
        imgui.begin_disabled(not self._write_value.can_write(token, formats))
        write = imgui.button(S.MONITOR_WRITE) or submitted
        imgui.end_disabled()
        imgui.same_line()
        read = imgui.button(S.MONITOR_READ)
        imgui.end_disabled()
        if self._write_value.error:
            imgui.text_colored(_ERROR_COLOR, self._write_value.error)

        if self._selected is None or not connected:
            return
        if write:
            payload = self._write_value.payload()
            if payload is not None:
                self._service.send_payload(self._selected, payload)
        if read:
            self._service.send_read(self._selected)

    def _render_table(self, gas: list[_GroupAddress]) -> None:
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

        clipper = imgui.ListClipper()
        clipper.begin(len(gas))
        while clipper.step():
            for i in range(clipper.display_start, clipper.display_end):
                ga = gas[i]
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
