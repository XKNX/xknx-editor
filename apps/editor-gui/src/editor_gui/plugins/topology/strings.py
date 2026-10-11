"""Topology plugin strings."""

from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("topology", _locale_dir)


class TopologyStrings:
    @property
    def PANEL_TOPOLOGY(self) -> str:
        return _("Topology")

    @property
    def TOPOLOGY_EMPTY(self) -> str:
        return _("No project open")

    @property
    def TOPOLOGY_UNASSIGNED(self) -> str:
        return _("unassigned")

    @property
    def TOPOLOGY_HINT(self) -> str:
        return _("Ctrl/Cmd+scroll to zoom. Click a device to open it.")

    @property
    def TOPOLOGY_BUILDING_MODE(self) -> str:
        return _("Building View")


S = TopologyStrings()
