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
    def SETTING_GATEWAY_IP_HINT(self) -> str:
        return _(
            "An open connection to the gateway connects to the new address once it is entered."
        )

    @property
    def SETTING_USE_PROJECT_BUS_CONNECTION(self) -> str:
        return _("Use the Project's Bus Connection")

    @property
    def SETTING_USE_PROJECT_BUS_CONNECTION_HINT(self) -> str:
        return _(
            "While a project is open whose backbone line has a Bus Connection (Topology), the "
            "connection goes through it instead of the gateway set here."
        )

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
    def SETTING_PROTECTED_GAS(self) -> str:
        return _("Protected Group Addresses")

    @property
    def SETTING_PROTECTED_GAS_APPLY(self) -> str:
        return _("Apply")

    @property
    def SETTING_PROTECTED_GAS_INVALID(self) -> str:
        return _("Not a group address or group: {entries}")

    @property
    def SETTING_PROTECTED_GAS_HINT(self) -> str:
        return _(
            "Nothing is ever sent to these group addresses, not even reads, e.g. the ones of "
            "gates or an alarm system: addresses like 1/2/3, or groups ending with a slash like "
            "7/0/, separated by spaces. MCP clients can add to the list but not take anything "
            "out."
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
    def SETTING_CONFIRMATION_TIMEOUT(self) -> str:
        return _("Confirmation Timeout (Seconds)")

    @property
    def SETTING_CONFIRMATION_TIMEOUT_HINT(self) -> str:
        return _(
            "How long to wait for the interface to confirm each frame it sends. A slow "
            "connection, such as a remote tunnel or a VPN, needs longer."
        )

    @property
    def SETTING_ACKNOWLEDGEMENT_TIMEOUT(self) -> str:
        return _("Acknowledgement Timeout (Seconds)")

    @property
    def SETTING_ACKNOWLEDGEMENT_TIMEOUT_HINT(self) -> str:
        return _(
            "How long to wait for a device to acknowledge a frame of a download before "
            "sending it again (up to three times). A device closes the connection after "
            "6 seconds without a frame, so a longer wait only helps where the answers "
            "are late, not lost."
        )

    @property
    def SETTING_RESPONSE_TIMEOUT(self) -> str:
        return _("Response Timeout (Seconds)")

    @property
    def SETTING_RESPONSE_TIMEOUT_HINT(self) -> str:
        return _(
            "How long to wait for a device's answer, e.g. to a memory or property read."
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
    def USB_MEDIUM(self) -> str:
        # {medium}: TP or RF, as lines name their medium
        return _("Medium: {medium}")

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
    def USB_DOMAIN_ADDRESS_LABEL(self) -> str:
        return _("Domain Address")

    @property
    def USB_DOMAIN_ADDRESS_FROM_LINE(self) -> str:
        # a drop-down of the project's RF lines; choosing one fills in its domain address
        return _("From Line")

    @property
    def USB_DOMAIN_ADDRESS_HINT(self) -> str:
        return _(
            "The interface sends with this domain address and receives only the telegrams of "
            "devices with it."
        )

    @property
    def USB_DOMAIN_ADDRESS_INVALID(self) -> str:
        return _(
            "{address} is not an RF domain address (4 and 8 hexadecimal digits, e.g. "
            "0001:23456789)."
        )

    @property
    def USB_DOMAIN_ADDRESS_BUSY(self) -> str:
        return _("The domain address of the interface is being set.")

    @property
    def USB_NOT_RF(self) -> str:
        return _("The connected USB interface is not a KNX RF interface.")

    @property
    def USB_DOMAIN_ADDRESS_FAILED(self) -> str:
        return _("The domain address could not be set: {error}")

    @property
    def USB_DOMAIN_ADDRESS_SETTING(self) -> str:
        return _("Setting the domain address…")

    @property
    def SETTING_USB_LOCAL_DEVICE(self) -> str:
        return _("Local Device Access")

    @property
    def SETTING_USB_LOCAL_DEVICE_HINT(self) -> str:
        return _(
            "Connects to the KNX device the USB interface is part of (e.g. an actuator with a "
            "USB port of its own) instead of the bus. Only this device can then be reached, "
            "for downloads and readouts; group telegrams, broadcasts and the bus monitor are "
            "not available. Needs a cEMI interface."
        )

    @property
    def USB_LOCAL_DEVICE_OPTION(self) -> str:
        # a checkbox: connect through the cEMI transport layer instead of to the bus
        return _("Connect to the interface's own device (local device access)")

    @property
    def STATUS_LOCAL_DEVICE(self) -> str:
        # {target}: the USB interface, e.g. "KNX-USB Interface (USB)"; {address}: the
        # individual address of its own device
        return _("{target}, local device access to {address}")

    @property
    def LOCAL_DEVICE_ADDRESS_UNKNOWN(self) -> str:
        # stands for the individual address of the interface's own device when it is unknown
        return _("(address unknown)")

    @property
    def USB_LOCAL_DEVICE_DETAILS(self) -> str:
        return _("Local device access: only this device is reached")

    @property
    def USB_LOCAL_DEVICE_SWITCHING(self) -> str:
        return _("Switching the connection…")

    @property
    def USB_LOCAL_DEVICE_FAILED(self) -> str:
        return _("The connection could not be switched: {error}")

    @property
    def USB_LOCAL_DEVICE_BUSY(self) -> str:
        return _("The connection of the interface is being switched.")

    @property
    def LOCAL_DEVICE_OTHER_DEVICE(self) -> str:
        # {local}: the individual address of the USB interface's own device
        return _(
            "Through local device access only the USB interface's own device {local} can be "
            "reached, not {address}."
        )

    @property
    def LOCAL_DEVICE_NO_GROUP_TELEGRAMS(self) -> str:
        return _(
            "Through local device access only the USB interface's own device {local} can be "
            "reached: group telegrams can't be sent."
        )

    @property
    def LOCAL_DEVICE_NO_BROADCASTS(self) -> str:
        return _(
            "Through local device access only the USB interface's own device {local} can be "
            "reached: broadcasts can't be sent."
        )

    @property
    def LOCAL_DEVICE_NO_BUS_MONITOR(self) -> str:
        return _(
            "Through local device access only the USB interface's own device {local} can be "
            "reached: the bus monitor can't run."
        )

    @property
    def LOCAL_DEVICE_SCAN_RANGE(self) -> str:
        return _(
            "Through local device access only the USB interface's own device {local} can be "
            "reached: scan from {local} to {local}."
        )

    @property
    def IP_SERVER_HEADER(self) -> str:
        return _("KNXnet/IP Server")

    @property
    def IP_SERVER_ENABLED(self) -> str:
        # a checkbox
        return _("Run a KNXnet/IP server for other tools (USB to IP bridge)")

    @property
    def IP_SERVER_HINT(self) -> str:
        return _(
            "Other tools (another editor, Home Assistant, a commissioning tool) connect to it "
            "over the network and use this USB interface as a KNXnet/IP interface. Each tunnel "
            "gets one of the tunnel addresses, which have to be free addresses of the "
            "interface's line. It runs while the editor is connected to the bus through the "
            "interface, and opens a network port: turn it on only where that is wanted."
        )

    @property
    def IP_SERVER_PORT(self) -> str:
        return _("Port")

    @property
    def IP_SERVER_LISTEN(self) -> str:
        return _("Listen On")

    @property
    def IP_SERVER_LISTEN_ALL(self) -> str:
        return _("All network interfaces")

    @property
    def IP_SERVER_LISTEN_LOCAL(self) -> str:
        return _("This computer only")

    @property
    def IP_SERVER_DISCOVERY(self) -> str:
        # a checkbox: the server answers the searches of KNXnet/IP clients
        return _("Answer searches (discovery)")

    @property
    def IP_SERVER_TCP(self) -> str:
        # a checkbox: tunnels over TCP besides UDP
        return _("Offer TCP")

    @property
    def IP_SERVER_ADDRESSES(self) -> str:
        return _("Tunnel Addresses")

    @property
    def IP_SERVER_SUGGEST(self) -> str:
        # fills in free addresses of the interface's line
        return _("Suggest")

    @property
    def IP_SERVER_NAME(self) -> str:
        return _("Name")

    @property
    def IP_SERVER_APPLY(self) -> str:
        return _("Apply")

    def IP_SERVER_RUNNING(self, count: int) -> str:
        # {port}: the network port; {count}: the open tunnels
        return _.plural(
            "KNXnet/IP server on port {port}, {count} tunnel",
            "KNXnet/IP server on port {port}, {count} tunnels",
            count,
        )

    @property
    def IP_SERVER_STOPPED(self) -> str:
        return _("The KNXnet/IP server is not running.")

    @property
    def IP_SERVER_NO_TUNNELS(self) -> str:
        return _("No tunnels open")

    @property
    def IP_SERVER_CHANNEL(self) -> str:
        # the communication channel of a tunnel
        return _("Channel")

    @property
    def IP_SERVER_CLIENT(self) -> str:
        return _("Client")

    @property
    def IP_SERVER_PROTOCOL(self) -> str:
        return _("Protocol")

    @property
    def IP_SERVER_ADDRESS(self) -> str:
        return _("Address")

    @property
    def IP_SERVER_NO_ADDRESSES(self) -> str:
        return _("At least one tunnel address is needed.")

    @property
    def IP_SERVER_ADDRESS_TWICE(self) -> str:
        return _("The tunnel address {address} is given twice.")

    @property
    def IP_SERVER_ADDRESS_INTERFACE(self) -> str:
        return _("{address} is the individual address of the interface itself.")

    @property
    def IP_SERVER_ADDRESS_LINE(self) -> str:
        # {line}: the line of the interface, e.g. 1.1
        return _("{address} is not on the line of the interface ({line}).")

    @property
    def IP_SERVER_ADDRESS_USED(self) -> str:
        return _("{address} is the individual address of the device {name}.")

    @property
    def IP_SERVER_PORT_INVALID(self) -> str:
        return _("{port} is not a port (1 to 65535).")

    @property
    def IP_SERVER_NEEDS_USB(self) -> str:
        return _(
            "The KNXnet/IP server runs only while the editor is connected through a KNX USB "
            "interface."
        )

    @property
    def IP_SERVER_NOT_LOCAL(self) -> str:
        return _(
            "The KNXnet/IP server doesn't run with local device access: the connection "
            "reaches only the interface's own device."
        )

    @property
    def IP_SERVER_BUSY(self) -> str:
        return _("The KNXnet/IP server is being started or stopped.")

    @property
    def IP_SERVER_FAILED(self) -> str:
        return _("The KNXnet/IP server could not start: {error}")

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

    @property
    def MENU_CONFIGURED_CONNECTIONS(self) -> str:
        return _("Configured Connections…")

    @property
    def CONFIGURED_TITLE(self) -> str:
        return _("Configured Connections")

    @property
    def CONFIGURED_NEW(self) -> str:
        return _("New")

    @property
    def CONFIGURED_DELETE(self) -> str:
        return _("Delete")

    @property
    def CONFIGURED_IMPORT(self) -> str:
        return _("Import…")

    @property
    def CONFIGURED_EXPORT(self) -> str:
        return _("Export…")

    @property
    def CONFIGURED_USE_AS_PROJECT(self) -> str:
        return _("Use as project connection")

    @property
    def CONFIGURED_PROJECT_TAG(self) -> str:
        return _("Project connection")

    @property
    def CONFIGURED_HOST(self) -> str:
        return _("IP Address or Host Name")

    @property
    def CONFIGURED_PROTOCOL_AUTO(self) -> str:
        return _("Automatic")

    @property
    def CONFIGURED_USE_NAT(self) -> str:
        return _("Use NAT mode")

    @property
    def CONFIGURED_NONE(self) -> str:
        return _("No configured connections yet.")

    @property
    def CONFIGURED_HOST_INVALID(self) -> str:
        return _("The address is neither an IP address nor a valid host name.")

    @property
    def CONFIGURED_DEFAULT_NAME(self) -> str:
        return _("IP Tunneling")

    @property
    def CONFIGURED_EXPORT_FAILED(self) -> str:
        return _("The file could not be written: {error}")

    @property
    def CONFIGURED_IMPORT_FAILED(self) -> str:
        return _("The file could not be read: {error}")

    @property
    def CONFIGURED_EXPORTED(self) -> str:
        return _("Connections exported to {name}.")

    @property
    def CONFIGURED_FILE_FILTER(self) -> str:
        return _("XML Files")

    @property
    def CONFIGURED_IMPORT_TITLE(self) -> str:
        return _("Import Connections")

    @property
    def CONFIGURED_EXPORT_TITLE(self) -> str:
        return _("Export Connections")

    @property
    def CONFIGURED_NO_PROJECT(self) -> str:
        return _("Open a project to use a connection as its connection.")

    @property
    def SETTING_CONFIGURED_CONNECTIONS_HINT(self) -> str:
        return _(
            "Interfaces entered once by their address: offered in the Gateway menu, in Select "
            "Connection and as a project's Bus Connection, also those a gateway search can't "
            "find."
        )

    @property
    def CONFIGURED_NAME(self) -> str:
        return _("Name")

    @property
    def CONFIGURED_PORT(self) -> str:
        return _("Port")

    @property
    def CONFIGURED_PROTOCOL(self) -> str:
        return _("Protocol")

    def CONFIGURED_IMPORTED(self, count: int) -> str:
        return _.plural(
            "{count} connection imported.", "{count} connections imported.", count
        ).format(count=count)

    def SETTING_CONFIGURED_COUNT(self, count: int) -> str:
        return _.plural("{count} connection", "{count} connections", count).format(
            count=count
        )


S = ConnectionStrings()
