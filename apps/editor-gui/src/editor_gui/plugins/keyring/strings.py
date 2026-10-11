"""Keyring plugin strings."""

from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("keyring", _locale_dir)


class KeyringStrings:
    @property
    def PANEL_KEYRING(self) -> str:
        return _("XKNX Secure")

    @property
    def MENU_SHOW_WINDOW(self) -> str:
        return _("Show Window")

    @property
    def KEYRING_EMPTY(self) -> str:
        return _("No keyring loaded")

    @property
    def KEYRING_IMPORT(self) -> str:
        return _("Import Keyring…")

    @property
    def KEYRING_EXPORT(self) -> str:
        return _("Export Keyring…")

    @property
    def KEYRING_EXPORT_SAVE(self) -> str:
        return _("Export")

    @property
    def KEYRING_EXPORT_HINT(self) -> str:
        return _("Set the password for the exported keyring:")

    @property
    def KEYRING_FILTER(self) -> str:
        return _("KNX Keyring (*.knxkeys)")

    @property
    def ALL_FILES(self) -> str:
        return _("All Files")

    @property
    def KEYRING_LOAD(self) -> str:
        return _("Load")

    @property
    def BTN_CANCEL(self) -> str:
        return _("Cancel")

    @property
    def KEYRING_CLOSE(self) -> str:
        return _("Close")

    @property
    def KEYRING_HEADER(self) -> str:
        return _("{project} — created by {by}")

    @property
    def KEYRING_BACKBONE(self) -> str:
        return _("Backbone")

    @property
    def KEYRING_INTERFACES(self) -> str:
        return _("Interfaces ({count})")

    @property
    def KEYRING_GROUP_ADDRESSES(self) -> str:
        return _("Group Address Keys ({count})")

    @property
    def KEYRING_DEVICES(self) -> str:
        return _("Devices ({count})")


S = KeyringStrings()
