"""Health plugin: a docked panel listing actionable commissioning checks for the open project."""

from editor_gui.plugins.base import PanelDefinition, PluginAPI
from editor_gui.plugins.health.service import HealthService
from editor_gui.plugins.health.strings import S
from editor_gui.plugins.health.ui import HealthPanel


class HealthPlugin:
    name = "health"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        self._service = HealthService(api.project)
        self._panel = HealthPanel(
            service=self._service,
            on_navigate=self._navigate,
            on_navigate_ga=api.project.request_group_address,
            is_open=lambda: api.project.is_open,
        )
        self._panels = [
            PanelDefinition(
                name="health",
                label=S.PANEL_HEALTH,
                dock="RightSpace",
                render=self._panel.render,
            ),
        ]

    def _navigate(self, node_id: int) -> None:
        device = self._api.project.find_device_by_node_id(node_id)
        if device is not None:
            self._api.project.selected_device = device
            self._api.project.focus_editor()

    @property
    def panels(self) -> list[PanelDefinition]:
        return self._panels

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass
