"""Topology plugin strings."""

from pathlib import Path

from editor_gui.strings import create_translator, modifier_key

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
        return _(
            "Drag to move, {key}+scroll to zoom. Click a device to open it."
        ).format(key=modifier_key())

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
            "Which devices talk to which over their group addresses: sending devices on the left, "
            "receiving devices on the right."
        )

    @property
    def TOPOLOGY_LINKS_HINT(self) -> str:
        return _(
            "Click to follow a device or group address, double-click to open a device. Drag to "
            "move, {key}+scroll to zoom."
        ).format(key=modifier_key())

    @property
    def TOPOLOGY_LINKS_HINT_TOUCH(self) -> str:
        return _(
            "Tap to follow a device or group address, double-tap to open a device. Zoom with two "
            "fingers."
        )

    @property
    def TOPOLOGY_NO_LINKS(self) -> str:
        return _("No linked devices to show.")

    @property
    def TOPOLOGY_SCOPE(self) -> str:
        return _("Show")

    @property
    def TOPOLOGY_SCOPE_ALL(self) -> str:
        return _("Whole Project")

    @property
    def TOPOLOGY_SCOPE_ROOMS(self) -> str:
        return _("Rooms")

    @property
    def TOPOLOGY_SCOPE_MAINS(self) -> str:
        return _("Main Groups")

    @property
    def TOPOLOGY_SCOPE_DEVICE(self) -> str:
        return _("Device: {device}")

    @property
    def TOPOLOGY_SCOPE_GA(self) -> str:
        return _("Group Address: {address}")

    @property
    def TOPOLOGY_BACK(self) -> str:
        return _("Back")

    @property
    def TOPOLOGY_FIT(self) -> str:
        return _("Fit")

    @property
    def TOPOLOGY_COL_SENDERS(self) -> str:
        return _("Sending")

    @property
    def TOPOLOGY_COL_GROUP_ADDRESSES(self) -> str:
        return _("Group Addresses")

    @property
    def TOPOLOGY_COL_RECEIVERS(self) -> str:
        return _("Receiving")

    @property
    def TOPOLOGY_COL_AREAS(self) -> str:
        return _("Areas")

    @property
    def TOPOLOGY_COL_LINES(self) -> str:
        return _("Lines")

    @property
    def TOPOLOGY_COL_DEVICES(self) -> str:
        return _("Devices")

    @property
    def TOPOLOGY_COL_BUILDINGS(self) -> str:
        return _("Buildings")

    @property
    def TOPOLOGY_COL_ROOMS(self) -> str:
        return _("Rooms")

    @property
    def TOPOLOGY_MORE(self) -> str:
        return _("+ {count} more")

    @property
    def TOPOLOGY_MORE_TIP(self) -> str:
        return _("{count} more devices outside the selection. Click to show them.")

    @property
    def TOPOLOGY_GA_TIP(self) -> str:
        return _("{address}\n{senders} sending, {receivers} receiving")

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
