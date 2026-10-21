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
    def TOPOLOGY_MODE_PHYSICAL(self) -> str:
        return _("Physical")

    @property
    def TOPOLOGY_MODE_BUILDING(self) -> str:
        return _("Building")

    @property
    def TOPOLOGY_MODE_LINKS(self) -> str:
        return _("Links")

    @property
    def TOPOLOGY_SHOW_LINKS_HINT(self) -> str:
        return _(
            "Each group address is a hub node; devices link to it (sender into the hub, hub out to "
            "receivers). Hover a device to see its group addresses, or a hub to see every device "
            "on it. Click a device to open it."
        )

    @property
    def TOPOLOGY_NO_LINKS(self) -> str:
        return _("No linked devices to show.")

    @property
    def TOPOLOGY_FILTER_ROOM(self) -> str:
        return _("Room")

    @property
    def TOPOLOGY_FILTER_GROUP(self) -> str:
        return _("Group")

    @property
    def TOPOLOGY_FILTER_ALL(self) -> str:
        return _("All")

    @property
    def TOPOLOGY_AREA(self) -> str:
        return _("Area {area}")

    @property
    def TOPOLOGY_LINE(self) -> str:
        return _("L {line}")

    @property
    def TOPOLOGY_HINT_TOUCH(self) -> str:
        return _("Zoom with two fingers. Tap a device to open it.")

    @property
    def TOPOLOGY_TIP_UNNAMED(self) -> str:
        return _("(unnamed)")

    @property
    def TOPOLOGY_TIP_ADDRESS(self) -> str:
        return _("Address: {address}")

    @property
    def TOPOLOGY_TIP_APPLICATION(self) -> str:
        return _("Application: {application}")


S = TopologyStrings()
