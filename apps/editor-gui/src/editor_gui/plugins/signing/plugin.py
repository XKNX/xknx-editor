"""Signing plugin: manage the .knxproj signing key (view / edit / save / extract from ETS DLL).

Not a docked tab: the menu-bar entry asks for the Signing Key dialog, which the app shows (the
export flow shows the same dialog on top of the Sign Export dialog)."""

from imgui_bundle import imgui

from editor_gui.plugins.base import PanelDefinition, PluginAPI
from editor_gui.plugins.signing.strings import S
from editor_gui.plugins.signing.ui import SigningPanel


class SigningPlugin:
    name = "signing"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        self._panel = SigningPanel(notify=api.notify)
        self._open_requested = False

    @property
    def panels(self) -> list[PanelDefinition]:
        return []  # not a docked tab — opened from the menu / the export window

    def open_window(self) -> None:
        """Ask for the Signing Key dialog (see :meth:`take_open_request`)."""
        self._open_requested = True

    def take_open_request(self) -> bool:
        """Whether the dialog was asked for since the last call."""
        requested, self._open_requested = self._open_requested, False
        return requested

    def render_contents(self) -> None:
        """Render the panel body, the content of the Signing Key dialog."""
        self._panel.render_contents()

    def render_menu(self) -> None:
        """The 'Signing' menu-bar entry."""
        if imgui.begin_menu(S.MENU):
            if imgui.menu_item(S.MENU_SHOW_WINDOW, "", False)[0]:
                self.open_window()
            imgui.end_menu()

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass
