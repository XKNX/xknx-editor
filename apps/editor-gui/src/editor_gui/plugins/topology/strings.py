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
        return _("Unassigned")

    @property
    def TOPOLOGY_HINT(self) -> str:
        return _("Ctrl/Cmd+scroll to zoom. Click a device to open it.")

    @property
    def TOPOLOGY_BUILDING_MODE(self) -> str:
        return _("Building View")

    @property
    def TOPOLOGY_AREA(self) -> str:
        return _("Area {area}")

    @property
    def TOPOLOGY_LINE(self) -> str:
        return _("L {line}")

    @property
    def TOPOLOGY_HINT_TOUCH(self) -> str:
        return _("Zoom with two fingers. Tap a device to open it.")


S = TopologyStrings()
