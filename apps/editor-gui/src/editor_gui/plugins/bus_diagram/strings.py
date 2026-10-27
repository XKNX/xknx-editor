"""Bus Diagram plugin strings."""

from pathlib import Path

from editor_gui.strings import create_translator, modifier_key

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("bus_diagram", _locale_dir)


class BusDiagramStrings:
    @property
    def PANEL_BUS_DIAGRAM(self) -> str:
        return _("Bus Diagram")

    @property
    def BUS_DIAGRAM_EMPTY(self) -> str:
        return _("No project open")

    @property
    def BUS_DIAGRAM_UNASSIGNED(self) -> str:
        return _("Unassigned")

    @property
    def BUS_DIAGRAM_HINT(self) -> str:
        return _(
            "Drag to move, {key}+scroll to zoom. Click a device to open it."
        ).format(key=modifier_key())

    @property
    def BUS_DIAGRAM_MODE_PHYSICAL(self) -> str:
        return _("Physical")

    @property
    def BUS_DIAGRAM_MODE_BUILDING(self) -> str:
        return _("Building")

    @property
    def BUS_DIAGRAM_MODE_LINKS(self) -> str:
        return _("Links")

    @property
    def BUS_DIAGRAM_SHOW_LINKS_HINT(self) -> str:
        return _(
            "Which devices talk to which over their group addresses: sending devices on the left, "
            "receiving devices on the right."
        )

    @property
    def BUS_DIAGRAM_LINKS_HINT(self) -> str:
        return _(
            "Click to follow a device, channel or group address, double-click to open a device, "
            "click the arrow of a device to list its group objects. Drag to move, {key}+scroll "
            "to zoom."
        ).format(key=modifier_key())

    @property
    def BUS_DIAGRAM_LINKS_HINT_TOUCH(self) -> str:
        return _(
            "Tap to follow a device, channel or group address, double-tap to open a device, tap "
            "the arrow of a device to list its group objects. Zoom with two fingers."
        )

    @property
    def BUS_DIAGRAM_NO_LINKS(self) -> str:
        return _("No linked devices to show.")

    @property
    def BUS_DIAGRAM_SCOPE(self) -> str:
        return _("Show")

    @property
    def BUS_DIAGRAM_SCOPE_ALL(self) -> str:
        return _("Whole Project")

    @property
    def BUS_DIAGRAM_SCOPE_ROOMS(self) -> str:
        return _("Rooms")

    @property
    def BUS_DIAGRAM_SCOPE_MAINS(self) -> str:
        return _("Main Groups")

    @property
    def BUS_DIAGRAM_SCOPE_DEVICE(self) -> str:
        return _("Device: {device}")

    @property
    def BUS_DIAGRAM_SCOPE_CHANNELS(self) -> str:
        return _("Channels")

    @property
    def BUS_DIAGRAM_SCOPE_CHANNEL(self) -> str:
        return _("Channel: {channel} ({device})")

    @property
    def BUS_DIAGRAM_SCOPE_GA(self) -> str:
        return _("Group Address: {address}")

    @property
    def BUS_DIAGRAM_OPEN_OBJECTS(self) -> str:
        return _("List the group objects")

    @property
    def BUS_DIAGRAM_CLOSE_OBJECTS(self) -> str:
        return _("Hide the group objects")

    @property
    def BUS_DIAGRAM_TIP_OBJECT(self) -> str:
        return _("Group object {number}: {name}")

    @property
    def BUS_DIAGRAM_TIP_CHANNEL(self) -> str:
        return _("Channel: {channel}")

    @property
    def BUS_DIAGRAM_BACK(self) -> str:
        return _("Back")

    @property
    def BUS_DIAGRAM_FIT(self) -> str:
        return _("Fit")

    @property
    def BUS_DIAGRAM_COL_SENDERS(self) -> str:
        return _("Sending")

    @property
    def BUS_DIAGRAM_COL_GROUP_ADDRESSES(self) -> str:
        return _("Group Addresses")

    @property
    def BUS_DIAGRAM_COL_RECEIVERS(self) -> str:
        return _("Receiving")

    @property
    def BUS_DIAGRAM_COL_AREAS(self) -> str:
        return _("Areas")

    @property
    def BUS_DIAGRAM_COL_LINES(self) -> str:
        return _("Lines")

    @property
    def BUS_DIAGRAM_COL_DEVICES(self) -> str:
        return _("Devices")

    @property
    def BUS_DIAGRAM_COL_BUILDINGS(self) -> str:
        return _("Buildings")

    @property
    def BUS_DIAGRAM_COL_ROOMS(self) -> str:
        return _("Rooms")

    @property
    def BUS_DIAGRAM_MORE(self) -> str:
        return _("+ {count} more")

    @property
    def BUS_DIAGRAM_MORE_TIP(self) -> str:
        return _("{count} more devices outside the selection. Click to show them.")

    @property
    def BUS_DIAGRAM_GA_TIP(self) -> str:
        return _("{address}\n{senders} sending, {receivers} receiving")

    @property
    def BUS_DIAGRAM_AREA(self) -> str:
        return _("Area {area}")

    @property
    def BUS_DIAGRAM_LINE(self) -> str:
        return _("L {line}")

    @property
    def BUS_DIAGRAM_HINT_TOUCH(self) -> str:
        return _("Zoom with two fingers. Tap a device to open it.")

    @property
    def BUS_DIAGRAM_TIP_UNNAMED(self) -> str:
        return _("(unnamed)")

    @property
    def BUS_DIAGRAM_TIP_ADDRESS(self) -> str:
        return _("Address: {address}")

    @property
    def BUS_DIAGRAM_TIP_APPLICATION(self) -> str:
        return _("Application: {application}")


S = BusDiagramStrings()
