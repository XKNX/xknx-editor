"""Monitor plugin: Group Monitor, Bus Monitor, Bus Activity and the group objects' values."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.monitor.formatting import (
    DptFormat,
    ProjectIndex,
    build_index,
    master_formats,
)
from editor_gui.plugins.monitor.frames import individual_address_text
from editor_gui.plugins.monitor.service import MonitorService
from editor_gui.plugins.monitor.strings import S
from editor_gui.plugins.monitor.ui import MonitorPanel
from editor_gui.regional import regional_format


class MonitorPlugin:
    name = "monitor"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        self._service = MonitorService(api.connection)
        self._service.set_logger(Logger(api.log, "monitor"))
        self._index = ProjectIndex()
        self._formats: tuple[int, dict[str, DptFormat]] = (0, {})
        self._panel = MonitorPanel(
            service=self._service,
            connection=api.connection,
            get_group_addresses=lambda: api.project.group_addresses,
            get_index=self._project_index,
            notify=api.notify,
            on_select_device=self._select_device,
        )
        connection = api.connection
        connection.add_raw_cemi_listener(self._service.on_raw_cemi)
        connection.add_sent_cemi_listener(self._service.on_sent_cemi)
        connection.add_connected_listener(self._service.on_connected)
        connection.add_disconnected_listener(self._service.on_disconnected)
        connection.add_link_state_listener(self._service.on_link_state)
        connection.add_busmonitor_listener(self._service.on_busmonitor_cemi)
        connection.add_busmonitor_state_listener(self._service.on_busmonitor_state)

    @property
    def service(self) -> MonitorService:
        """The live monitor service (shared with the embedded MCP server)."""
        return self._service

    @property
    def monitor_extended(self) -> bool:
        return self._panel.monitor_extended

    def set_monitor_extended(self, value: bool) -> None:
        self._panel.set_monitor_extended(value)

    def set_layout_toggler(self, toggle: Callable[[], None]) -> None:
        self._panel.set_layout_toggler(toggle)

    def toggle_monitor_extended(self) -> None:
        self._panel.toggle_monitor_extended()

    def _select_device(self, address: int) -> None:
        """Select the project device with individual address ``address``, if there is one."""
        device = self._api.project.find_device_by_address(
            individual_address_text(address)
        )
        if device is not None:
            self._api.project.selected_device = device

    def _project_index(self) -> ProjectIndex:
        """Names, DPTs and building structure of the open project, rebuilt when it changes."""
        project = self._api.project
        master = self._api.connection.master
        decimal = regional_format().decimal
        key = (
            project.revision,
            str(project.path),
            project.is_open,
            id(master),
            decimal,
        )
        if self._index.revision == key or project.loading:
            return self._index
        if self._formats[0] != id(master):
            self._formats = (id(master), master_formats(master))
        style: Any = None
        devices: list[Any] = []
        spaces: list[Any] = []
        if project.is_open:
            try:
                style = project.group_address_style
            except Exception:
                style = None
            devices = [*project.devices, *project.unloaded_devices]
            spaces = list(project.get_space_tree())
        self._index = build_index(
            revision=key,
            group_addresses=project.group_addresses,
            devices=devices,
            spaces=spaces,
            ga_style=style,
            formats=self._formats[1],
            decimal=decimal,
        )
        return self._index

    @property
    def panels(self) -> list[PanelDefinition]:
        return [
            PanelDefinition(
                name="monitor",
                label=S.PANEL_MONITOR,
                dock="BottomSpace",
                render=self._panel.render,
            )
        ]

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        self._service.stop_replay()
