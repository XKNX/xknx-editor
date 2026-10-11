from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("connection", _locale_dir)


class ConnectionStrings:
    @property
    def MENU_CONNECTION(self) -> str:
        return _("Gateway")

    @property
    def MENU_CONNECT(self) -> str:
        return _("Connect")

    @property
    def MENU_DISCONNECT(self) -> str:
        return _("Disconnect")

    @property
    def SECTION_DISCOVERED(self) -> str:
        return _("Discovered Gateways")

    @property
    def SECTION_DIAGNOSTICS(self) -> str:
        return _("Diagnostics")

    @property
    def MENU_READ_PROGMODE(self) -> str:
        return _("Check for Devices in Programming Mode")

    @property
    def PROGMODE_TITLE(self) -> str:
        return _("Devices in Programming Mode")

    @property
    def PROGMODE_RESCAN(self) -> str:
        return _("Rescan")

    @property
    def PROGMODE_SEARCHING(self) -> str:
        return _("Searching the bus…")

    @property
    def PROGMODE_HINT(self) -> str:
        return _(
            "Press the programming button on a device to put it into programming mode, then rescan."
        )

    @property
    def PROGMODE_NONE(self) -> str:
        return _("No device is in programming mode.")

    @property
    def PROGMODE_SCAN_FAILED(self) -> str:
        return _("The scan could not be completed: {error}")

    @property
    def PROGMODE_NO_CONFIRM(self) -> str:
        return _(
            "The interface did not confirm the scan request (bus busy or interface "
            "overloaded). Please try again."
        )

    @property
    def PROGMODE_COUNT(self) -> str:
        return _("{count} device(s) in programming mode")

    @property
    def PROGMODE_READ_FAILED(self) -> str:
        return _("Answered the scan, but reading its details failed: {error}")

    @property
    def PROGMODE_IN_PROJECT(self) -> str:
        return _("In this project: {name}")

    @property
    def PROGMODE_NOT_IN_PROJECT(self) -> str:
        return _("Not in this project")

    @property
    def PROGMODE_UNPROGRAMMED(self) -> str:
        return _("Unprogrammed device (factory default address 15.15.255)")

    @property
    def PROGMODE_SELECT(self) -> str:
        return _("Select in Project")

    @property
    def PROGMODE_MANUFACTURER(self) -> str:
        return _("Manufacturer")

    @property
    def PROGMODE_PRODUCT(self) -> str:
        return _("Product (catalog)")

    @property
    def PROGMODE_APPLICATION(self) -> str:
        return _("Application")

    @property
    def PROGMODE_APP_VERSION(self) -> str:
        return _("Application Version")

    @property
    def PROGMODE_MASK(self) -> str:
        return _("Mask Version")

    @property
    def PROGMODE_SERIAL(self) -> str:
        return _("Serial Number")

    @property
    def PROGMODE_ORDER(self) -> str:
        return _("Order Number")

    @property
    def PROGMODE_HARDWARE(self) -> str:
        return _("Hardware Type")

    @property
    def PROGMODE_STATUS(self) -> str:
        return _("Status")

    @property
    def PROGMODE_STATUS_OK(self) -> str:
        return _("No fault")

    @property
    def PROGMODE_FLAG_ON(self) -> str:
        return _("Programming mode active")

    @property
    def NO_GATEWAYS_FOUND(self) -> str:
        return _("No gateways found")

    @property
    def SECTION_MANUAL(self) -> str:
        return _("Manual Connection")

    @property
    def STATUS_CONNECTED(self) -> str:
        return _("Connected: {ip}")

    @property
    def STATUS_CONNECTED_TO(self) -> str:
        return _("Connected to {ip}")

    @property
    def STATUS_DISCONNECTED(self) -> str:
        return _("Disconnected")

    @property
    def PANEL_GATEWAY(self) -> str:
        return _("Gateway")

    @property
    def GW_CONNECTING(self) -> str:
        return _("Connecting…")

    @property
    def GW_ERROR(self) -> str:
        return _("Connection failed")

    @property
    def GW_SCAN(self) -> str:
        return _("Scan")

    @property
    def GW_SETTINGS(self) -> str:
        return _("Settings")

    @property
    def GW_ROUTING(self) -> str:
        return _("Use multicast routing (instead of tunneling)")

    @property
    def GW_IP(self) -> str:
        return _("Gateway IP")

    @property
    def GW_MULTICAST(self) -> str:
        return _("Multicast Group")

    @property
    def GW_APPLY(self) -> str:
        return _("Apply")


S = ConnectionStrings()
