"""Signing plugin: manage the .knxproj signing key (view / edit / save / extract from ETS DLL).

Like the keyring, this is not a docked tab: a menu-bar entry opens a window on demand, and the
export flow can open the same window via :meth:`open_window`."""

from imgui_bundle import imgui

from editor_gui.plugins.base import PanelDefinition, PluginAPI
from editor_gui.plugins.signing.strings import S
from editor_gui.plugins.signing.ui import SigningPanel
from editor_gui.widgets.dialog import begin_tool_window


class SigningPlugin:
    name = "signing"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        self._panel = SigningPanel(notify=api.notify)
        self._window_open = False

    @property
    def panels(self) -> list[PanelDefinition]:
        return []  # not a docked tab — opened from the menu / the export window

    def open_window(self) -> None:
        """Open the standalone signing window (called from the menu)."""
        self._window_open = True

    def render_contents(self) -> None:
        """Render the panel body; used to embed it as a nested modal inside the export dialog."""
        self._panel.render_contents()

    def render_menu(self) -> None:
        """The 'Signing' menu-bar entry."""
        if imgui.begin_menu(S.MENU):
            if imgui.menu_item(S.MENU_SHOW_WINDOW, "", self._window_open)[0]:
                self._window_open = not self._window_open
            imgui.end_menu()

    def render_window(self) -> None:
        """The signing window; called every frame from the overlay pass."""
        if not self._window_open:
            return
        expanded, open_state = begin_tool_window(
            S.WINDOW_TITLE, True, width=40 * 16, height=24 * 16
        )
        self._window_open = bool(open_state)
        if expanded:
            self._panel.render_contents()
        imgui.end()

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass
