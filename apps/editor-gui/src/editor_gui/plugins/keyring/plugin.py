"""Keyring / KNX Secure plugin: import and browse a ``.knxkeys`` keyring.

KNX Secure is used only occasionally, so it is not a docked tab: it lives as a menu-bar entry
that opens a separate window on demand (the window carries the import + the interface/GA/device
tables)."""

from imgui_bundle import imgui

from editor_gui.plugins.base import Logger, PanelDefinition, PluginAPI
from editor_gui.plugins.keyring.service import KeyringService
from editor_gui.plugins.keyring.strings import S
from editor_gui.plugins.keyring.ui import KeyringPanel
from editor_gui.widgets.dialog import begin_tool_window


class KeyringPlugin:
    name = "keyring"

    def __init__(self, api: PluginAPI) -> None:
        self._api = api
        self._service = KeyringService()
        self._service.set_logger(Logger(api.log, "keyring"))
        self._panel = KeyringPanel(
            service=self._service,
            get_group_addresses=lambda: api.project.group_addresses,
        )
        self._window_open = False
        self._was_loaded = False

    @property
    def service(self) -> KeyringService:
        """The live keyring service (shared with the embedded MCP server)."""
        return self._service

    @property
    def panels(self) -> list[PanelDefinition]:
        return []  # not a docked tab — opened from the menu into its own window

    def render_menu(self) -> None:
        """The 'XKNX Secure' menu-bar entry (placed between MCP and Help)."""
        if imgui.begin_menu(S.PANEL_KEYRING):
            if self._panel.has_keyring():
                if imgui.menu_item(S.MENU_SHOW_WINDOW, "", self._window_open)[0]:
                    self._window_open = not self._window_open
                if imgui.menu_item(S.KEYRING_EXPORT, "", False)[0]:
                    self._panel.begin_export()  # save dialog + password modal
                if imgui.menu_item(S.KEYRING_CLOSE, "", False)[0]:
                    self._service.clear()
                    self._window_open = False
            elif imgui.menu_item(S.KEYRING_IMPORT, "", False)[0]:
                self._panel.begin_import()  # file dialog; no window
            imgui.end_menu()

    def render_window(self) -> None:
        """Import/export prompts (small modals) + the content window; called from the overlay pass."""
        self._panel.render_import_prompt()  # file-dialog poll + password modal (no window)
        self._panel.render_export_prompt()  # save-dialog poll + password modal (no window)
        loaded = self._panel.has_keyring()
        if loaded and not self._was_loaded:
            self._window_open = True  # a keyring was just loaded -> show its content
        self._was_loaded = loaded
        if not (loaded and self._window_open):
            return
        expanded, open_state = begin_tool_window(
            S.PANEL_KEYRING, True, width=34 * 16, height=26 * 16
        )
        self._window_open = bool(open_state)
        if expanded:
            self._panel.render_contents()
        imgui.end()

    def on_load(self) -> None:
        pass

    def on_unload(self) -> None:
        pass
