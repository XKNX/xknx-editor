"""Group monitor plugin: read/write KNX group-address values with DPT decoding."""

from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.monitor.service import MonitorService
from editor_gui.plugins.monitor.strings import S
from editor_gui.plugins.monitor.ui import MonitorPanel


class MonitorPlugin:
    name = "monitor"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        self._service = MonitorService(api.connection)
        self._service.set_logger(Logger(api.log, "monitor"))
        self._panel = MonitorPanel(
            service=self._service,
            get_group_addresses=lambda: api.project.group_addresses,
            is_connected=lambda: api.connection.xknx is not None,
        )
        api.connection.add_raw_cemi_listener(self._service.on_raw_cemi)
        api.connection.add_connected_listener(self._service.clear)

    @property
    def service(self) -> MonitorService:
        """The live monitor service (shared with the embedded MCP server)."""
        return self._service

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
        pass
