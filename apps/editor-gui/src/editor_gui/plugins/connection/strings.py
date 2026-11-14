from pathlib import Path

from editor_gui.strings import create_translator

_locale_dir = Path(__file__).parent / "locales"
_ = create_translator("connection", _locale_dir)


class ConnectionStrings:
    @property
    def MENU_GATEWAY(self) -> str:
        return _("Gateway")

    @property
    def MENU_CONNECT(self) -> str:
        return _("Connect")

    @property
    def MENU_DISCONNECT(self) -> str:
        return _("Disconnect")

    @property
    def SETTING_USE_SERIAL_NUMBER(self) -> str:
        return _("Use Serial Number for Downloads")

    @property
    def SETTING_CONNECTION_TYPE(self) -> str:
        return _("Connection Type")

    @property
    def SETTING_CONNECTION_TYPE_HINT(self) -> str:
        return _("Applies on the next connect, like the Gateway menu's choice.")

    @property
    def SETTING_GATEWAY_IP(self) -> str:
        return _("Gateway IP")

    @property
    def SETTING_USB_INTERFACE(self) -> str:
        return _("USB Interface")

    @property
    def SETTING_CONNECT_AT_STARTUP(self) -> str:
        return _("Connect at Startup")

    @property
    def SETTING_CONNECT_AT_STARTUP_HINT(self) -> str:
        return _(
            "At start, the editor looks for KNX interfaces and connects to the one used last. "
            "Turned off, it connects only when you choose."
        )

    @property
    def SETTING_BLOCK_BUS_WRITES(self) -> str:
        return _("Block Bus Writes")

    @property
    def SETTING_BLOCK_BUS_WRITES_HINT(self) -> str:
        return _(
            "Reads only: nothing that would change something on the bus is sent, such as "
            "downloads, address changes, restarts or group writes. This holds for MCP clients "
            "too, which can turn it on but not off. Reads of KNX Data Secure devices are "
            "blocked as well."
        )

    @property
    def STATUS_WRITES_BLOCKED(self) -> str:
        return _("Bus writes blocked")

    @property
    def STATUS_WRITES_BLOCKED_HINT(self) -> str:
        return _(
            "Block Bus Writes is on: the editor reads from the bus but changes nothing there. "
            "Click to change it in the settings."
        )

    @property
    def SETTING_USE_SERIAL_NUMBER_HINT(self) -> str:
        return _(
            "A device whose serial number is known gets its individual address without "
            "pressing its programming button."
        )

    @property
    def SECTION_DISCOVERED(self) -> str:
        return _("Discovered Gateways")

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

    def PROGMODE_COUNT(self, count: int) -> str:
        return _.plural(
            "{count} device in programming mode",
            "{count} devices in programming mode",
            count,
        )

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
    def SECTION_USB(self) -> str:
        return _("USB Interfaces")

    @property
    def NO_USB_INTERFACES(self) -> str:
        return _("No USB interfaces found")

    @property
    def USB_NOT_ON_BUS(self) -> str:
        return _("Not connected to the bus")

    @property
    def USB_SERIAL_NUMBER(self) -> str:
        return _("Serial Number: {serial}")

    @property
    def MENU_USB_INTERFACE(self) -> str:
        return _("USB Interface…")

    @property
    def USB_INTERFACE_TITLE(self) -> str:
        return _("USB Interface")

    @property
    def USB_INTERFACE_CLOSE(self) -> str:
        return _("Close")

    @property
    def USB_MAX_APDU_LENGTH(self) -> str:
        return _("Maximum Telegram Length (APDU): {length}")

    @property
    def USB_EMI_TYPE(self) -> str:
        # {emi}: cEMI, EMI2 or EMI1, the message format used with the interface
        return _("EMI Type: {emi}")

    @property
    def USB_SUPPORTED_EMI_TYPES(self) -> str:
        # {types}: e.g. "cEMI, EMI2"
        return _("Supported EMI Types: {types}")

    @property
    def USB_MASK_VERSION(self) -> str:
        # {mask}: four hexadecimal digits, e.g. 07B0
        return _("Mask Version: {mask}")

    @property
    def USB_ADDRESS_LABEL(self) -> str:
        return _("Individual Address")

    @property
    def USB_ADDRESS_APPLY(self) -> str:
        # sets the individual address in the field on the interface
        return _("Apply")

    @property
    def USB_ADDRESS_FREE(self) -> str:
        return _("Address Free?")

    @property
    def USB_ADDRESS_IS_FREE(self) -> str:
        return _("The individual address {address} is not used by another device.")

    @property
    def USB_ADDRESS_NOT_FREE(self) -> str:
        return _(
            "The individual address {address} is already in use by another device on the bus."
        )

    @property
    def USB_ADDRESS_INVALID(self) -> str:
        return _("{address} is not an individual address (area.line.device).")

    @property
    def USB_ADDRESS_BUSY(self) -> str:
        return _("The individual address of the interface is being set or checked.")

    @property
    def USB_NOT_CONNECTED_HERE(self) -> str:
        return _("Not connected through a USB interface.")

    @property
    def USB_ADDRESS_FAILED(self) -> str:
        return _("The individual address could not be set: {error}")

    @property
    def USB_ADDRESS_CHECK_FAILED(self) -> str:
        return _("The individual address could not be checked: {error}")

    @property
    def USB_ADDRESS_SETTING(self) -> str:
        return _("Setting the individual address…")

    @property
    def USB_ADDRESS_CHECKING(self) -> str:
        return _("Checking the individual address on the bus…")

    @property
    def USB_ADDRESS_HINT_VOLATILE(self) -> str:
        return _(
            "This interface forgets its individual address when unplugged, so it is set "
            "again on every connect."
        )

    @property
    def SECTION_MANUAL(self) -> str:
        return _("Manual Connection")

    @property
    def CONN_TYPE_TUNNELING(self) -> str:
        return _("UDP tunneling")

    @property
    def CONN_TYPE_TCP_TUNNELING(self) -> str:
        return _("TCP tunneling")

    @property
    def CONN_TYPE_ROUTING(self) -> str:
        return _("Routing")

    @property
    def CONN_TYPE_USB(self) -> str:
        # a KNX USB interface, as the Connection Type setting names it
        return _("USB")

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
