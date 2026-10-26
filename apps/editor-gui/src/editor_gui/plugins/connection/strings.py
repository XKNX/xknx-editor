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
    def MENU_USE_SERIAL_NUMBER(self) -> str:
        return _("Use Serial Number for Downloads")

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
        return _("Product (Catalog)")

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

    @property
    def STATUS_BUSMONITOR(self) -> str:
        return _("Bus monitor: {ip}")

    @property
    def STATUS_BUSMONITOR_HINT(self) -> str:
        return _(
            "The gateway is connected in bus monitor mode. Nothing can be sent or downloaded "
            "until the bus monitor is stopped."
        )

    @property
    def STATUS_BUSMONITOR_BLOCKED(self) -> str:
        return _("Not possible while the bus monitor is running")

    @property
    def MENU_STOP_BUSMONITOR(self) -> str:
        return _("Stop Bus Monitor")

    @property
    def CONNECT_SECURE_NO_CREDENTIALS(self) -> str:
        return _(
            "The interface requires KNX IP Secure, but neither the project nor a loaded keyring has its passwords."
        )

    @property
    def STATUS_CONNECTING(self) -> str:
        return _("Connecting…")

    @property
    def STATUS_ERROR(self) -> str:
        return _("Error: {error}")

    @property
    def STATUS_UNKNOWN_ERROR(self) -> str:
        return _("Unknown error")

    @property
    def STATUS_CONNECTION_FAILED(self) -> str:
        return _("Connection failed")

    @property
    def GATEWAY_SECTION(self) -> str:
        return _("Gateway")

    @property
    def GATEWAY_NAME(self) -> str:
        return _("Name: {name}")

    @property
    def GATEWAY_ADDRESS(self) -> str:
        return _("KNX Address: {address}")

    @property
    def GATEWAY_CORE_VERSION(self) -> str:
        return _("Core Version: {version}")

    @property
    def GATEWAY_SERVICES(self) -> str:
        return _("Services: {services}")

    @property
    def MENU_RETRY(self) -> str:
        return _("Retry")

    @property
    def SELECT_CONNECTION_TITLE(self) -> str:
        return _("Select Connection")

    @property
    def SELECT_CONNECTION_AUTOMATIC(self) -> str:
        return _("XKNX Editor could not determine an available connection.")

    @property
    def SELECT_CONNECTION_SPECIFIC(self) -> str:
        return _("The connection {name} is not available.")

    @property
    def SELECT_CONNECTION_INSTEAD(self) -> str:
        return _("You may use any of the connections listed below, instead.")

    @property
    def SELECT_CONNECTION_FAILED(self) -> str:
        return _("{name}: {error}")

    @property
    def SELECT_CONNECTION_CANCEL(self) -> str:
        return _("Cancel")

    @property
    def BUS_CONNECTION_UNSUPPORTED(self) -> str:
        return _("This kind of connection is not supported.")


S = ConnectionStrings()
