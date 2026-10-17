"""Cockpit plugin strings."""

from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("cockpit", _locale_dir)


class CockpitStrings:
    @property
    def PANEL_COCKPIT(self) -> str:
        return _("Device Overview")

    @property
    def COCKPIT_EMPTY(self) -> str:
        return _("No project open")

    @property
    def COCKPIT_SEARCH(self) -> str:
        return _("Filter devices…")

    @property
    def COCKPIT_ATTENTION_ONLY(self) -> str:
        return _("Needs Attention Only")

    @property
    def COCKPIT_SKIPPED(self) -> str:
        return _("{count} device(s) not in catalog")

    @property
    def COCKPIT_COL_ADDRESS(self) -> str:
        return _("Address")

    @property
    def COCKPIT_COL_NAME(self) -> str:
        return _("Name")

    @property
    def COCKPIT_COL_PRODUCT(self) -> str:
        return _("Product")

    @property
    def COCKPIT_COL_STATUS(self) -> str:
        return _("Status")

    @property
    def COCKPIT_OK(self) -> str:
        return _("OK")

    @property
    def COCKPIT_COL_LOADED(self) -> str:
        return _("Loaded")

    @property
    def COCKPIT_LOADED_FULL(self) -> str:
        return _("loaded")

    @property
    def COCKPIT_LOADED_NONE(self) -> str:
        return _("not loaded")

    @property
    def COCKPIT_LOADED_PARTIAL(self) -> str:
        return _("partial ({done}/{total})")

    @property
    def COCKPIT_LOADED_TOOLTIP_IA(self) -> str:
        return _("Individual Address")

    @property
    def COCKPIT_LOADED_TOOLTIP_APP(self) -> str:
        return _("Application Program")

    @property
    def COCKPIT_LOADED_TOOLTIP_COMM(self) -> str:
        return _("Communication Part")

    @property
    def COCKPIT_LOADED_TOOLTIP_MEDIUM(self) -> str:
        return _("Medium Config")

    @property
    def COCKPIT_LOADED_TOOLTIP_PARAMS(self) -> str:
        return _("Parameters")

    @property
    def COCKPIT_LOADED_TOOLTIP_SERIAL(self) -> str:
        return _("Serial: {serial}")

    @property
    def COCKPIT_LOADED_TOOLTIP_LAST_DOWNLOAD(self) -> str:
        return _("Last Download: {when}")


S = CockpitStrings()
