"""Recover plugin: reconstruct a project by reading devices off the bus.

The recover view is a dockable panel that opens next to the Topology view (the
``MainDockSpace`` dock); the welcome card's Recover button asks the app to show it in front (see
:meth:`take_focus_request`). It scans an address range, identifies each device against the
catalog, reads it back, and adds the recovered devices to a project."""

from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.recover.service import RecoverService
from editor_gui.plugins.recover.strings import S
from editor_gui.plugins.recover.ui import RecoverPanel


class RecoverPlugin:
    name = "recover"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        self._service = RecoverService(api)
        self._service.set_logger(Logger(api.log, "recover"))
        self._panel = RecoverPanel(self._service)
        self._focus_requested = False

    @property
    def panels(self) -> list[PanelDefinition]:
        # Docked next to the topology/devices tree, not a floating window.
        return [
            PanelDefinition(
                name="recover",
                label=S.WINDOW_TITLE,
                dock="MainDockSpace",
                render=self._panel.render,
            )
        ]

    def open_window(self) -> None:
        """Ask for the recover panel in front (also the welcome screen's Recover button)."""
        self._focus_requested = True

    def take_focus_request(self) -> bool:
        """Whether the panel was asked for since the last call. The app brings it to the front:
        a panel behind another tab is not drawn, so it could not do that itself."""
        requested, self._focus_requested = self._focus_requested, False
        return requested

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass
