"""KNXnet/IP tunnelling in bus monitor mode and decoding of the frames it delivers.

Self-contained stand-in for xknx's bus monitor support; names and shapes follow xknx's API so
this module can be replaced by xknx imports later:

- :class:`BusmonitorConnection` opens a tunnel with the bus monitor layer (CRI layer 80h,
  KNXnet/IP Tunnelling 03.08.04 §2.2.4) and passes every received cEMI frame to a callback
  (xknx: ``ConnectionConfig(busmonitor=True)`` and
  ``cemi_handler.register_busmonitor_frame_received_cb``);
- :func:`busmon_frame_from_knx` parses a cEMI L_Busmon.ind (message code 2Bh) into
  :class:`CEMIInfo` and :class:`CEMIBusmonData` (xknx: ``CEMIFrame.from_knx``), the additional
  information following EMI/IMI 03.06.03 §4.1.4.3 and §4.1.5.8;
- :func:`parse_tp1_frame` decodes the raw TP1 frame: standard and extended data frames with their
  check octet, and the short acknowledgement characters (Communication Medium TP1 03.02.02 §2.2).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum

from xknx import XKNX
from xknx.cemi import CEMIFrame, CEMILData
from xknx.cemi.cemi_frame import CEMIInfo as _XknxCEMIInfo
from xknx.core import XknxConnectionState
from xknx.exceptions import (
    CommunicationError,
    CouldNotParseCEMI,
    UnsupportedCEMIMessage,
)
from xknx.io import util
from xknx.io.request_response import Connect
from xknx.io.tunnel import TCPTunnel, UDPTunnel
from xknx.knxip import ConnectRequestInformation, ErrorCode, TunnellingLayer
from xknx.telegram import GroupAddress, IndividualAddress, Telegram

# cEMI message code of L_Busmon.ind.
L_BUSMON_IND = 0x2B
DEFAULT_GATEWAY_PORT = 3671
SEND_ERROR = "Sending is not possible in bus monitor mode"


class CEMIAdditionalInfoType(IntEnum):
    """Type ids of the cEMI additional information."""

    PL_MEDIUM_INFO = 0x01
    RF_MEDIUM_INFO = 0x02
    BUSMONITOR_STATUS_INFO = 0x03
    TIMESTAMP_RELATIVE = 0x04
    TIME_DELAY_UNTIL_SENDING = 0x05
    EXTENDED_RELATIVE_TIMESTAMP = 0x06
    BIBAT_INFO = 0x07
    RF_MULTI_INFO = 0x08
    PREAMBLE_POSTAMBLE = 0x09
    RF_FAST_ACK_INFO = 0x0A
    MANUFACTURER_SPECIFIC = 0xFE


class CEMIPriority(IntEnum):
    """Frame priority, the p1 p0 bits of the control field."""

    SYSTEM = 0
    NORMAL = 1
    URGENT = 2
    LOW = 3


class CEMIFrameType(IntEnum):
    """The FT bit of the control field."""

    EXTENDED = 0
    STANDARD = 1


class TP1Acknowledgement(IntEnum):
    """Short acknowledgement characters of TP1."""

    ACK = 0xCC
    NAK = 0x0C
    BUSY = 0xC0
    # NAK and BUSY sent at the same time; handled as BUSY.
    NAK_BUSY = 0x00


class RequestResponseError(CommunicationError):
    """The tunnelling server rejected the connect request."""

    def __init__(self, message: str, error_code: ErrorCode | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


# --- connecting ------------------------------------------------------------------------------


def _busmonitor_cri() -> ConnectRequestInformation:
    return ConnectRequestInformation(knx_layer=TunnellingLayer.BUSMONITOR_LAYER)


class BusmonitorUDPTunnel(UDPTunnel):
    """A UDP tunnel opened with the bus monitor layer. It never sends KNX frames."""

    async def _connect_request(self) -> bool:
        connect = Connect(
            transport=self.transport, local_hpai=self.local_hpai, cri=_busmonitor_cri()
        )
        await connect.start()
        if not connect.success:
            raise RequestResponseError(
                f"ConnectRequest failed. Status code: {connect.response_status_code}",
                connect.response_status_code,
            )
        self.communication_channel = connect.communication_channel
        self._data_endpoint_addr = (
            None
            if connect.data_endpoint.route_back
            else connect.data_endpoint.addr_tuple
        )
        self._src_address = connect.crd.individual_address or IndividualAddress(0)
        self.xknx.current_address = self._src_address
        return True

    async def send_cemi(self, cemi: CEMIFrame) -> None:
        raise CommunicationError(SEND_ERROR)


class BusmonitorTCPTunnel(TCPTunnel):
    """A TCP tunnel opened with the bus monitor layer. It never sends KNX frames."""

    async def _connect_request(self) -> bool:
        connect = Connect(
            transport=self.transport, local_hpai=self.local_hpai, cri=_busmonitor_cri()
        )
        await connect.start()
        if not connect.success:
            raise RequestResponseError(
                f"ConnectRequest failed. Status code: {connect.response_status_code}",
                connect.response_status_code,
            )
        self.communication_channel = connect.communication_channel
        self._data_endpoint_addr = None
        self._src_address = connect.crd.individual_address or IndividualAddress(0)
        self.xknx.current_address = self._src_address
        return True

    async def send_cemi(self, cemi: CEMIFrame) -> None:
        raise CommunicationError(SEND_ERROR)


class BusmonitorConnection:
    """A bus monitor tunnel to one KNXnet/IP tunnelling server.

    ``on_frame`` receives every raw cEMI frame (L_Busmon.ind) on the event loop's thread;
    ``on_state`` receives ``True``/``False`` when the tunnel is re-established or lost after
    :meth:`connect` returned."""

    def __init__(
        self,
        gateway_ip: str,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        *,
        on_frame: Callable[[bytes], None],
        on_state: Callable[[bool], None] | None = None,
        tcp: bool = False,
        local_ip: str | None = None,
        auto_reconnect: bool = True,
    ) -> None:
        self.gateway_ip = gateway_ip
        self.gateway_port = gateway_port
        self.tcp = tcp
        self._local_ip = local_ip
        self._on_frame = on_frame
        self._on_state = on_state
        self._auto_reconnect = auto_reconnect
        self._xknx = XKNX()
        self._tunnel: BusmonitorUDPTunnel | BusmonitorTCPTunnel | None = None
        self._unregister: Callable[[], None] | None = None

    @property
    def connected(self) -> bool:
        return self._tunnel is not None

    @property
    def individual_address(self) -> IndividualAddress:
        return self._xknx.current_address

    async def connect(self) -> None:
        """Open the tunnel. Raises :class:`RequestResponseError` when the server rejects it and
        :class:`CommunicationError` when it cannot be reached."""
        if self.tcp:
            tunnel: BusmonitorUDPTunnel | BusmonitorTCPTunnel = BusmonitorTCPTunnel(
                self._xknx,
                cemi_received_callback=self._on_frame,
                gateway_ip=self.gateway_ip,
                gateway_port=self.gateway_port,
                auto_reconnect=self._auto_reconnect,
            )
        else:
            local_ip = self._local_ip or util.find_local_ip(gateway_ip=self.gateway_ip)
            route_back = False
            if local_ip is None:
                local_ip = await util.get_default_local_ip(self.gateway_ip)
                if local_ip is None:
                    raise CommunicationError("No network interface found")
                route_back = True
            tunnel = BusmonitorUDPTunnel(
                self._xknx,
                cemi_received_callback=self._on_frame,
                gateway_ip=self.gateway_ip,
                gateway_port=self.gateway_port,
                local_ip=local_ip,
                route_back=route_back,
                auto_reconnect=self._auto_reconnect,
            )
        try:
            await tunnel.connect()
        except CommunicationError as err:
            cause = err.__cause__
            if isinstance(cause, RequestResponseError):
                raise cause from None
            raise
        self._tunnel = tunnel
        if self._on_state is not None:
            self._unregister = (
                self._xknx.connection_manager.register_connection_state_changed_cb(
                    self._state_changed
                )
            )

    def _state_changed(self, state: XknxConnectionState) -> None:
        if self._on_state is None:
            return
        if state is XknxConnectionState.CONNECTED:
            self._on_state(True)
        elif state is XknxConnectionState.DISCONNECTED:
            self._on_state(False)

    async def disconnect(self) -> None:
        if self._unregister is not None:
            self._unregister()
            self._unregister = None
        tunnel, self._tunnel = self._tunnel, None
        if tunnel is not None:
            await tunnel.disconnect()


def rejection_reason(error_code: ErrorCode | None) -> str:
    """``"unsupported"`` when the server does not offer bus monitor tunnels, ``"busy"`` when
    another tunnel is open, else ``""``."""
    if error_code in (
        ErrorCode.E_TUNNELLING_LAYER,
        ErrorCode.E_CONNECTION_OPTION,
        ErrorCode.E_CONNECTION_TYPE,
    ):
        return "unsupported"
    if error_code in (
        ErrorCode.E_NO_MORE_CONNECTIONS,
        ErrorCode.E_NO_MORE_UNIQUE_CONNECTIONS,
        ErrorCode.E_CONNECTION_IN_USE,
    ):
        return "busy"
    return ""


# --- cEMI L_Busmon.ind -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CEMIBusmonitorStatus:
    """Busmonitor error flags (additional information 03h): F B P overflow L sss."""

    frame_error: bool
    bit_error: bool
    parity_error: bool
    overflow: bool
    lost: bool
    sequence_number: int

    @classmethod
    def from_knx(cls, octet: int) -> CEMIBusmonitorStatus:
        return cls(
            frame_error=bool(octet & 0x80),
            bit_error=bool(octet & 0x40),
            parity_error=bool(octet & 0x20),
            overflow=bool(octet & 0x10),
            lost=bool(octet & 0x08),
            sequence_number=octet & 0x07,
        )


class CEMIInfo(_XknxCEMIInfo):
    """cEMI additional information with access to its items."""

    def items(self) -> list[tuple[int, bytes]]:
        """``(type id, data)`` of every item. Raises ``CouldNotParseCEMI`` when malformed."""
        result: list[tuple[int, bytes]] = []
        raw = self.raw
        pos = 0
        while pos < len(raw):
            if pos + 2 > len(raw):
                raise CouldNotParseCEMI("additional information item truncated")
            kind, size = raw[pos], raw[pos + 1]
            if pos + 2 + size > len(raw):
                raise CouldNotParseCEMI("additional information item exceeds its field")
            result.append((kind, bytes(raw[pos + 2 : pos + 2 + size])))
            pos += 2 + size
        return result

    def get(self, type_id: int) -> bytes | None:
        for kind, data in self.items():
            if kind == type_id:
                return data
        return None

    @property
    def busmonitor_status(self) -> CEMIBusmonitorStatus | None:
        data = self.get(CEMIAdditionalInfoType.BUSMONITOR_STATUS_INFO)
        return CEMIBusmonitorStatus.from_knx(data[0]) if data else None

    @property
    def timestamp_relative(self) -> int | None:
        data = self.get(CEMIAdditionalInfoType.TIMESTAMP_RELATIVE)
        return int.from_bytes(data, "big") if data else None

    @property
    def extended_relative_timestamp(self) -> int | None:
        data = self.get(CEMIAdditionalInfoType.EXTENDED_RELATIVE_TIMESTAMP)
        return int.from_bytes(data, "big") if data else None

    @staticmethod
    def from_knx(raw: bytes) -> tuple[CEMIInfo, bytes]:
        if not raw:
            raise CouldNotParseCEMI("additional information length missing")
        length = raw[0]
        if length + 1 > len(raw):
            raise CouldNotParseCEMI("additional information exceeds the frame")
        return CEMIInfo(bytes(raw[1 : length + 1])), bytes(raw[length + 1 :])


@dataclass(frozen=True, slots=True)
class CEMIBusmonData:
    """The data of an L_Busmon.ind: the frame as received on the medium, check octet included."""

    raw_frame: bytes

    def to_knx(self) -> bytes:
        return self.raw_frame


@dataclass(frozen=True, slots=True)
class BusmonFrame:
    """A parsed L_Busmon.ind (xknx: ``CEMIFrame`` with ``code=L_BUSMON_IND``)."""

    info: CEMIInfo
    data: CEMIBusmonData
    code: int = L_BUSMON_IND

    def to_knx(self) -> bytes:
        return bytes((self.code,)) + self.info.to_knx() + self.data.to_knx()


def busmon_frame_from_knx(raw_cemi: bytes) -> BusmonFrame:
    """Parse a cEMI L_Busmon.ind. Raises ``CouldNotParseCEMI`` for anything else."""
    if not raw_cemi or raw_cemi[0] != L_BUSMON_IND:
        raise CouldNotParseCEMI("not an L_Busmon.ind")
    info, frame = CEMIInfo.from_knx(raw_cemi[1:])
    return BusmonFrame(info=info, data=CEMIBusmonData(raw_frame=frame))


def build_busmon_ind(
    frame: bytes,
    *,
    status: int | None = None,
    timestamp: int | None = None,
    extended_timestamp: int | None = None,
) -> bytes:
    """A cEMI L_Busmon.ind carrying ``frame`` and the given additional information."""
    info = b""
    if status is not None:
        info += bytes((CEMIAdditionalInfoType.BUSMONITOR_STATUS_INFO, 1, status & 0xFF))
    if timestamp is not None:
        info += bytes(
            (CEMIAdditionalInfoType.TIMESTAMP_RELATIVE, 2)
        ) + timestamp.to_bytes(2, "big")
    if extended_timestamp is not None:
        info += bytes(
            (CEMIAdditionalInfoType.EXTENDED_RELATIVE_TIMESTAMP, 4)
        ) + extended_timestamp.to_bytes(4, "big")
    return bytes((L_BUSMON_IND, len(info))) + info + frame


# --- TP1 frames -------------------------------------------------------------------------------


def tp1_checksum(octets: bytes) -> int:
    """The check octet over ``octets``: odd parity per bit, i.e. NOT XOR."""
    value = 0
    for octet in octets:
        value ^= octet
    return ~value & 0xFF


@dataclass(frozen=True, slots=True)
class TP1DataFrame:
    """A TP1 L_Data frame as received on the medium. The header fields are always available;
    :meth:`cemi_l_data` and :meth:`telegram` parse the TPDU and raise when they cannot."""

    src_addr: IndividualAddress
    dst_addr: GroupAddress | IndividualAddress
    # TPCI octet onwards.
    tpdu: bytes
    priority: CEMIPriority
    # True when the repeat bit is 0.
    repeated: bool
    hop_count: int
    frame_type: CEMIFrameType
    # The received check octet.
    checksum: int
    # The octets the check octet covers.
    checked: bytes

    @property
    def address_type(self) -> type[GroupAddress] | type[IndividualAddress]:
        return type(self.dst_addr)

    @property
    def checksum_valid(self) -> bool:
        return tp1_checksum(self.checked) == self.checksum

    def control_fields(self) -> tuple[int, int]:
        """The frame's Ctrl1 and Ctrl2 in cEMI form."""
        ctrl1 = (
            self.frame_type << 7
            | (0 if self.repeated else 0x20)
            | 0x10
            | self.priority << 2
        )
        group = isinstance(self.dst_addr, GroupAddress)
        ctrl2 = (0x80 if group else 0) | (self.hop_count & 0x07) << 4
        return ctrl1, ctrl2

    def cemi_ldata_raw(self) -> bytes:
        """The frame's fields in cEMI L_Data form: Ctrl1 Ctrl2 SA DA L TPDU."""
        ctrl1, ctrl2 = self.control_fields()
        return (
            bytes((ctrl1, ctrl2))
            + self.src_addr.to_knx()
            + self.dst_addr.to_knx()
            + bytes((max(len(self.tpdu) - 1, 0),))
            + self.tpdu
        )

    def cemi_l_data(self) -> CEMILData:
        return CEMILData.from_knx(self.cemi_ldata_raw())

    def telegram(self) -> Telegram:
        return self.cemi_l_data().telegram()


def parse_tp1_frame(raw: bytes) -> TP1DataFrame | TP1Acknowledgement:
    """Decode a raw TP1 frame. A single octet is an acknowledgement. Raises
    ``CouldNotParseCEMI`` for malformed frames and ``UnsupportedCEMIMessage`` for L_Poll_Data and
    LTE frames."""
    if len(raw) == 1:
        try:
            return TP1Acknowledgement(raw[0])
        except ValueError:
            raise CouldNotParseCEMI(f"unknown acknowledgement {raw[0]:#04x}") from None
    if not raw:
        raise CouldNotParseCEMI("empty TP1 frame")
    ctrl = raw[0]
    if ctrl == 0xF0:
        raise UnsupportedCEMIMessage("L_Poll_Data frames are not supported")
    # L_Data frame control field: FT 0 r 1 p1 p0 0 0.
    if ctrl & 0x53 != 0x10:
        raise CouldNotParseCEMI(f"unknown TP1 control field {ctrl:#04x}")
    standard = bool(ctrl & 0x80)
    if standard:
        if len(raw) < 7:
            raise CouldNotParseCEMI("TP1 standard frame too short")
        group = bool(raw[5] & 0x80)
        hop_count = (raw[5] >> 4) & 0x07
        length = raw[5] & 0x0F
        addresses = raw[1:5]
        start = 6
    else:
        if len(raw) < 8:
            raise CouldNotParseCEMI("TP1 extended frame too short")
        ctrle = raw[1]
        if ctrle & 0x0F:
            raise UnsupportedCEMIMessage("LTE frames are not supported")
        group = bool(ctrle & 0x80)
        hop_count = (ctrle >> 4) & 0x07
        length = raw[6]
        addresses = raw[2:6]
        start = 7
    end = start + length + 1
    if len(raw) < end + 1:
        raise CouldNotParseCEMI("TP1 frame shorter than its length")
    destination = int.from_bytes(addresses[2:4], "big")
    return TP1DataFrame(
        src_addr=IndividualAddress(int.from_bytes(addresses[0:2], "big")),
        dst_addr=GroupAddress(destination) if group else IndividualAddress(destination),
        tpdu=bytes(raw[start:end]),
        priority=CEMIPriority((ctrl >> 2) & 0x03),
        repeated=not ctrl & 0x20,
        hop_count=hop_count,
        frame_type=CEMIFrameType.STANDARD if standard else CEMIFrameType.EXTENDED,
        checksum=raw[end],
        checked=bytes(raw[:end]),
    )


def encode_tp1(
    *,
    source: int,
    destination: int,
    group: bool,
    tpdu: bytes,
    priority: int = CEMIPriority.LOW,
    hop_count: int = 6,
    repeated: bool = False,
    extended: bool = False,
) -> bytes:
    """A TP1 L_Data frame with its check octet."""
    length = len(tpdu) - 1
    ctrl = 0x10 | (priority & 0x03) << 2 | (0 if repeated else 0x20)
    at = 0x80 if group else 0
    addresses = source.to_bytes(2, "big") + destination.to_bytes(2, "big")
    if extended or length > 15:
        body = (
            bytes((ctrl, at | (hop_count & 0x07) << 4))
            + addresses
            + bytes((length,))
            + tpdu
        )
    else:
        body = (
            bytes((ctrl | 0x80,))
            + addresses
            + bytes((at | (hop_count & 0x07) << 4 | length,))
            + tpdu
        )
    return body + bytes((tp1_checksum(body),))
