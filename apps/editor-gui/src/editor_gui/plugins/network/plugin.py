from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.network.service import NetworkService
from editor_gui.plugins.network.strings import S
from editor_gui.plugins.network.ui import CaptureState as UICaptureState
from editor_gui.plugins.network.ui import NetworkPanel


class NetworkPlugin:
    name = "network"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        self._service = NetworkService()
        self._service.set_logger(Logger(api.log, "network"))
        api.connection.add_raw_cemi_listener(self._service.add_raw)
        api.connection.add_connected_listener(self._service.start)
        self._panel = NetworkPanel(
            get_telegrams=lambda: self._service.telegrams,
            get_cemi_records=lambda: self._service.cemi_records,
            get_capture_state=lambda: UICaptureState(self._service.state.value),
            on_start=self._service.start,
            on_stop=self._service.stop,
            on_clear=self._service.clear,
            on_focus_source=self._on_focus_source,
            is_connected=lambda: self._api.connection.xknx is not None,
            get_ga_names=self._ga_names,
            get_ga_dpts=self._ga_dpts,
        )
        self._panels = [
            PanelDefinition(
                name="network",
                label=S.PANEL_NETWORK,
                dock="BottomSpace",
                render=self._panel.render,
            ),
        ]

    def _ga_names(self) -> dict[int, str]:
        """Map raw group-address value -> name for the loaded project (empty if none).

        Keyed by the raw 16-bit value (not the formatted string) so it matches xknx telegram
        destinations regardless of the project's 3-level/2-level/free address style."""
        return {ga.raw: ga.name for ga in self._api.project.group_addresses if ga.name}

    def _ga_dpts(self) -> dict[int, str]:
        """Map raw group-address value -> DPT for the loaded project, used to decode bus values."""
        return {
            ga.raw: ga.datapoint_type
            for ga in self._api.project.group_addresses
            if ga.datapoint_type
        }

    def _on_focus_source(self, address: str) -> None:
        device = self._api.project.find_device_by_address(address)
        if device:
            self._api.project.selected_device = device

    @property
    def service(self) -> NetworkService:
        """The live network-capture service (shared with the embedded MCP server)."""
        return self._service

    @property
    def panels(self) -> list[PanelDefinition]:
        return self._panels

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass
