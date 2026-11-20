"""The cEMI Transport Layer and the M-Bus and BatiBus tunnels of KNX USB interfaces (the copy
of ``xknx.io.usb``, its tests as they are upstream but for the connection configuration the copy
leaves out: the editor's ``ObservableUSBInterfaceThreaded`` stands in for it)."""

import asyncio
from collections.abc import Callable
from unittest.mock import Mock

import pytest
from xknx import XKNX
from xknx.cemi.cemi_frame import CEMIFrame, CEMILData
from xknx.cemi.const import CEMIErrorCode, CEMIMessageCode
from xknx.core.connection_state import XknxConnectionState
from xknx.exceptions import CommunicationError
from xknx.telegram import GroupAddress, IndividualAddress, Telegram, tpci
from xknx.telegram.apci import (
    DeviceDescriptorRead,
    DeviceDescriptorResponse,
    GroupValueRead,
    PropertyValueRead,
)

from editor_gui.plugins.connection.usb import ObservableUSBInterfaceThreaded, UsbTarget
from editor_gui.plugins.connection.xknx_usb import (
    EXTRANEOUS_PROTOCOLS,
    ProtocolID,
    USBTransferFrame,
    USBTunnel,
)
from editor_gui.plugins.connection.xknx_usb import tunnel as usb_tunnel
from editor_gui.plugins.connection.xknx_usb.const import EMIID, DeviceFeature
from editor_gui.plugins.connection.xknx_usb.transport_layer import (
    TransportLayerMapping,
)

from .conftest import FakeHIDModule, make_emi_interface

_DEVICE = IndividualAddress("1.0.2")
_TOOL = IndividualAddress("1.0.2")
_OTHER = IndividualAddress("1.0.7")
# A_DeviceDescriptor_Response of mask version 07B0h
_DESCRIPTOR_RESPONSE = bytes.fromhex("034007b0")
_TARGET = UsbTarget(0x16DE, 0x008E, "00C501053FB2", "KNX-USB Interface")


def _request(telegram: Telegram) -> bytes:
    """The L_Data.req of ``telegram`` the way the cEMI handler sends it."""
    return CEMIFrame(
        code=CEMIMessageCode.L_DATA_REQ,
        data=CEMILData.init_from_telegram(telegram, src_addr=_TOOL),
    ).to_knx()


def _received(raw_cemi: bytes) -> Telegram:
    """The telegram of a received L_Data frame."""
    frame = CEMIFrame.from_knx(raw_cemi)
    assert isinstance(frame.data, CEMILData)
    return frame.data.telegram()


async def _until(condition: Callable[[], bool], timeout: float = 2.0) -> None:
    """Wait for something done by the reader thread."""
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


@pytest.fixture(autouse=True)
def short_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Don't wait long for answers the fake interface doesn't give."""
    monkeypatch.setattr(usb_tunnel, "DEVICE_FEATURE_GET_TIMEOUT", 0.2)
    monkeypatch.setattr(usb_tunnel, "LOCAL_MANAGEMENT_TIMEOUT", 0.2)


class TestTransportLayerMapping:
    """Mapping L_Data telegrams to cEMI Transport Layer messages and back."""

    def test_connect_and_disconnect_stay_local(self) -> None:
        mapping = TransportLayerMapping(_DEVICE)
        for control in (tpci.TConnect(), tpci.TDisconnect(), tpci.TAck(5)):
            request = _request(Telegram(destination_address=_DEVICE, tpci=control))
            message, received = mapping.outgoing(request)
            assert message is None
            (confirmation,) = received
            assert confirmation == bytes((0x2E,)) + request[1:]

    def test_connected_data(self) -> None:
        mapping = TransportLayerMapping(_DEVICE)
        request = _request(
            Telegram(
                destination_address=_DEVICE,
                tpci=tpci.TDataConnected(sequence_number=3),
                payload=DeviceDescriptorRead(0),
            )
        )
        message, (confirmation, ack) = mapping.outgoing(request)
        # message code, no additional information, reserved, L, APCI without the TPCI
        assert message == bytes.fromhex("4100 000000000000 01 0300")
        assert CEMIFrame.from_knx(confirmation).code is CEMIMessageCode.L_DATA_CON
        acknowledgement = _received(ack)
        assert acknowledgement.source_address == _DEVICE
        assert acknowledgement.destination_address == _TOOL
        assert acknowledgement.tpci == tpci.TAck(sequence_number=3)

    def test_individual_data(self) -> None:
        mapping = TransportLayerMapping(_DEVICE)
        request = _request(
            Telegram(
                destination_address=_DEVICE,
                tpci=tpci.TDataIndividual(),
                payload=PropertyValueRead(0, 11, 1, 1),
            )
        )
        message, received = mapping.outgoing(request)
        assert message is not None
        assert message[0] == 0x4A
        assert message[2:8] == bytes(6)
        apdu = PropertyValueRead(0, 11, 1, 1).to_knx()
        assert message[8] == len(apdu) - 1
        assert message[9:] == apdu
        assert len(received) == 1

    def test_only_the_own_device(self) -> None:
        mapping = TransportLayerMapping(_DEVICE)
        with pytest.raises(CommunicationError, match="group telegrams"):
            mapping.outgoing(
                _request(
                    Telegram(
                        destination_address=GroupAddress("1/2/3"),
                        payload=GroupValueRead(),
                    )
                )
            )
        with pytest.raises(CommunicationError, match=r"not 1\.0\.7"):
            mapping.outgoing(
                _request(Telegram(destination_address=_OTHER, tpci=tpci.TConnect()))
            )
        with pytest.raises(CommunicationError, match="Only L_Data requests"):
            mapping.outgoing(bytes.fromhex("fc000801341001"))
        with pytest.raises(CommunicationError, match="too short"):
            mapping.outgoing(bytes.fromhex("1100bc60"))

    def test_unknown_device_address(self) -> None:
        mapping = TransportLayerMapping(None)
        mapping.outgoing(
            _request(Telegram(destination_address=_OTHER, tpci=tpci.TConnect()))
        )
        assert mapping.device_address == _OTHER
        indication = mapping.incoming(
            bytes.fromhex("8900 000000000000 03") + _DESCRIPTOR_RESPONSE, _TOOL
        )
        assert indication is not None
        assert _received(indication).source_address == _OTHER

    def test_incoming(self) -> None:
        mapping = TransportLayerMapping(_DEVICE)
        connected = bytes.fromhex("8900 000000000000 03") + _DESCRIPTOR_RESPONSE
        numbers: list[int] = []
        for _ in range(17):
            indication = mapping.incoming(connected, _TOOL)
            assert indication is not None
            telegram = _received(indication)
            assert isinstance(telegram.tpci, tpci.TDataConnected)
            numbers.append(telegram.tpci.sequence_number)
            assert telegram.payload == DeviceDescriptorResponse(0, 0x07B0)
            assert telegram.source_address == _DEVICE
            assert telegram.destination_address == _TOOL
        assert numbers == [*range(16), 0]
        # a new connection starts again at 0
        mapping.outgoing(
            _request(Telegram(destination_address=_DEVICE, tpci=tpci.TConnect()))
        )
        indication = mapping.incoming(connected, _TOOL)
        assert indication is not None
        assert _received(indication).tpci == tpci.TDataConnected(sequence_number=0)
        individual = mapping.incoming(
            bytes.fromhex("9400 000000000000 03") + _DESCRIPTOR_RESPONSE, _TOOL
        )
        assert individual is not None
        assert _received(individual).tpci == tpci.TDataIndividual()

    def test_long_apdu_is_an_extended_frame(self) -> None:
        mapping = TransportLayerMapping(_DEVICE)
        apdu = bytes.fromhex("03d6") + bytes(30)
        indication = mapping.incoming(
            bytes.fromhex("8900 000000000000") + bytes((len(apdu) - 1,)) + apdu, _TOOL
        )
        assert indication is not None
        assert indication[2] & 0x80 == 0
        assert _received(indication).payload is not None

    def test_incoming_other_messages(self) -> None:
        mapping = TransportLayerMapping(_DEVICE)
        assert mapping.incoming(b"", _TOOL) is None
        assert mapping.incoming(bytes.fromhex("2900bc60"), _TOOL) is None
        assert mapping.incoming(bytes.fromhex("8900 0000"), _TOOL) is None
        assert (
            mapping.incoming(bytes.fromhex("8900 000000000000 05 0340"), _TOOL) is None
        )


def _own_device(
    requests: list[tuple[int, bytes]],
) -> Callable[[int, bytes], tuple[int, bytes] | None]:
    """The application layer of the interface's own device: it answers a device descriptor
    read."""

    def answer(code: int, apdu: bytes) -> tuple[int, bytes] | None:
        requests.append((code, apdu))
        if code == 0x41 and apdu[:2] == b"\x03\x00":
            return 0x89, _DESCRIPTOR_RESPONSE
        return None

    return answer


class TestUSBTransportLayer:
    """A USB tunnel in the cEMI Transport Layer against a simulated interface."""

    def setup_method(self) -> None:
        self.xknx = XKNX()
        self.cemi_received = Mock()

    def tunnel(self, **kwargs: object) -> USBTunnel:
        """A tunnel in the cEMI Transport Layer."""
        return USBTunnel(
            self.xknx,
            cemi_received_callback=self.cemi_received,
            auto_reconnect=False,
            transport_layer=True,
            **kwargs,  # type: ignore[arg-type]
        )

    async def test_connect_and_disconnect(self, fake_hid: FakeHIDModule) -> None:
        tunnel = self.tunnel()
        await tunnel.connect()
        interface = fake_hid.interface
        assert interface.written_cemi()[:2] == [
            bytes.fromhex("f600080134100106"),  # PID_COMM_MODE := cEMI Transport Layer
            bytes.fromhex("fc000801341001"),  # read back
        ]
        assert tunnel.info is not None
        assert tunnel.info.transport_layer
        await tunnel.disconnect()
        assert interface.written_cemi()[-1] == bytes.fromhex("f6000801341001ff")
        assert interface.properties[(8, 52)] == b"\xff"

    @pytest.mark.parametrize(
        ("error_code", "message"),
        [
            (CEMIErrorCode.CEMI_ERROR_ILLEGAL_COMMAND, "does not support the cEMI"),
            (CEMIErrorCode.CEMI_ERROR_TEMPORARILY_READ_ONLY, "already has another"),
            (CEMIErrorCode.CEMI_ERROR_VOID_DP, "has no communication mode"),
            (CEMIErrorCode.CEMI_ERROR_MEMORY, "Writing property PID_COMM_MODE failed"),
        ],
    )
    async def test_refused(
        self, fake_hid: FakeHIDModule, error_code: CEMIErrorCode, message: str
    ) -> None:
        fake_hid.interface.transport_layer_refusal = error_code
        with pytest.raises(CommunicationError, match=message):
            await self.tunnel().connect()

    async def test_needs_cemi(self, fake_hid: FakeHIDModule) -> None:
        make_emi_interface(fake_hid.interface, EMIID.EMI2, 0x0025)
        with pytest.raises(CommunicationError, match="needs cEMI"):
            await self.tunnel().connect()

    def test_not_with_busmonitor(self) -> None:
        with pytest.raises(ValueError, match="bus monitor"):
            self.tunnel(busmonitor=True)

    async def test_without_bus(self, fake_hid: FakeHIDModule) -> None:
        fake_hid.interface.features[DeviceFeature.BUS_CONNECTION_STATUS] = b"\x00"
        states: list[XknxConnectionState] = []
        self.xknx.connection_manager.register_connection_state_changed_cb(states.append)
        tunnel = self.tunnel()
        await tunnel.connect()
        assert states[-1] is XknxConnectionState.CONNECTED
        await tunnel.send_cemi(
            CEMIFrame.from_knx(
                _request(Telegram(destination_address=_DEVICE, tpci=tpci.TConnect()))
            )
        )
        await tunnel.disconnect()

    async def test_device_management(self, fake_hid: FakeHIDModule) -> None:
        """xknx's management reads the own device through the editor's USB connection."""
        interface = fake_hid.interface
        requests: list[tuple[int, bytes]] = []
        interface.own_device = _own_device(requests)
        xknx = XKNX()
        connection = ObservableUSBInterfaceThreaded(xknx, _TARGET, transport_layer=True)
        xknx.knxip_interface = connection
        await connection.start()
        try:
            info = connection.usb_info()
            assert info is not None and info.transport_layer
            assert info.individual_address == _DEVICE
            async with xknx.management.connection(_DEVICE) as device:
                response = await device.request(DeviceDescriptorRead(0))
            assert response.payload == DeviceDescriptorResponse(0, 0x07B0)
            assert requests == [(0x41, b"\x03\x00")]
            # the device's answer was acknowledged locally: nothing else went out
            assert [cemi[0] for cemi in interface.written_cemi()].count(0x41) == 1
            with pytest.raises(CommunicationError):
                await connection.send_cemi(
                    CEMIFrame.from_knx(
                        _request(
                            Telegram(
                                destination_address=GroupAddress("1/2/3"),
                                payload=GroupValueRead(),
                            )
                        )
                    )
                )
        finally:
            await connection.stop()
        assert interface.properties[(8, 52)] == b"\xff"

    async def test_other_frames_are_ignored(self, fake_hid: FakeHIDModule) -> None:
        tunnel = self.tunnel()
        await tunnel.connect()
        self.cemi_received.reset_mock()
        fake_hid.interface.send_cemi(bytes.fromhex("2900bce01005010201 00 0080"))
        fake_hid.interface.send_cemi(
            bytes.fromhex("8900 000000000000 03") + _DESCRIPTOR_RESPONSE
        )
        await _until(lambda: self.cemi_received.call_count == 1)
        (raw_cemi,) = self.cemi_received.call_args.args
        assert _received(raw_cemi).payload == DeviceDescriptorResponse(0, 0x07B0)
        await tunnel.disconnect()

    async def test_disconnect_without_answer(self, fake_hid: FakeHIDModule) -> None:
        tunnel = self.tunnel()
        await tunnel.connect()
        fake_hid.interface.answers = False
        await tunnel.disconnect()
        assert fake_hid.interface.properties[(8, 52)] == b"\x06"
        with pytest.raises(CommunicationError, match="not connected"):
            await tunnel.send_cemi(
                CEMIFrame.from_knx(
                    _request(
                        Telegram(destination_address=_DEVICE, tpci=tpci.TConnect())
                    )
                )
            )

    async def test_keep_alive_without_answer(
        self, fake_hid: FakeHIDModule, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(usb_tunnel, "KEEP_ALIVE_INTERVAL", 0.05)
        monkeypatch.setattr(usb_tunnel, "LOCAL_MANAGEMENT_TIMEOUT", 0.05)
        interface = fake_hid.interface
        tunnel = self.tunnel()
        await tunnel.connect()
        mode_read = bytes.fromhex("fc000801341001")
        interface.answers = False
        start = interface.written_cemi().count(mode_read)
        await _until(lambda: interface.written_cemi().count(mode_read) >= start + 2)
        interface.answers = True
        await tunnel.disconnect()

    async def test_keep_alive(
        self, fake_hid: FakeHIDModule, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(usb_tunnel, "KEEP_ALIVE_INTERVAL", 0.05)
        interface = fake_hid.interface
        tunnel = self.tunnel()
        await tunnel.connect()
        mode_reads = bytes.fromhex("fc000801341001")
        start = interface.written_cemi().count(mode_reads)
        await _until(lambda: interface.written_cemi().count(mode_reads) >= start + 2)
        # the interface ended the connection on its own: it is set up again
        interface.properties[(8, 52)] = b"\x00"
        await _until(lambda: interface.properties[(8, 52)] == b"\x06")
        await tunnel.disconnect()


class TestExtraneousFrames:
    """The M-Bus and BatiBus tunnels."""

    async def test_send_and_receive(self, fake_hid: FakeHIDModule) -> None:
        frames: list[USBTransferFrame] = []
        tunnel = USBTunnel(
            XKNX(),
            cemi_received_callback=Mock(),
            auto_reconnect=False,
            extraneous_frame_callback=frames.append,
        )
        await tunnel.connect()
        sent = [
            USBTransferFrame(
                protocol=protocol, service=0x00, body=bytes.fromhex("44ae0c")
            )
            for protocol in sorted(EXTRANEOUS_PROTOCOLS)
        ]
        for frame in sent:
            await tunnel.send_extraneous_frame(frame)
        await _until(lambda: len(frames) == 2)
        assert frames == sent
        with pytest.raises(ValueError, match="Not an M-Bus or BatiBus"):
            await tunnel.send_extraneous_frame(
                USBTransferFrame(
                    protocol=ProtocolID.KNX_TUNNEL, service=EMIID.CEMI, body=b"\x11"
                )
            )
        await tunnel.disconnect()
        with pytest.raises(CommunicationError, match="not connected"):
            await tunnel.send_extraneous_frame(sent[0])

    async def test_ignored_without_callback(self, fake_hid: FakeHIDModule) -> None:
        tunnel = USBTunnel(XKNX(), cemi_received_callback=Mock(), auto_reconnect=False)
        await tunnel.connect()
        await tunnel.send_extraneous_frame(
            USBTransferFrame(protocol=ProtocolID.M_BUS_TUNNEL, service=0, body=b"\x01")
        )
        await asyncio.sleep(0.05)
        await tunnel.disconnect()

    async def test_through_the_editors_connection(
        self, fake_hid: FakeHIDModule
    ) -> None:
        """The callback is given to the editor's USB connection, and frames are sent through
        it (from the connection thread)."""
        frames: list[USBTransferFrame] = []
        connection = ObservableUSBInterfaceThreaded(
            XKNX(), _TARGET, extraneous_frame_callback=frames.append
        )
        await connection.start()
        try:
            frame = USBTransferFrame(
                protocol=ProtocolID.BATIBUS_TUNNEL, service=0, body=b"\x02"
            )
            await connection.send_extraneous_frame(frame)
            await _until(lambda: frames == [frame])
        finally:
            await connection.stop()

    async def test_only_while_connected(self) -> None:
        """The editor's USB connection has no M-Bus tunnel before it is connected."""
        connection = ObservableUSBInterfaceThreaded(XKNX(), _TARGET)
        with pytest.raises(CommunicationError, match="not connected"):
            await connection.send_extraneous_frame(
                USBTransferFrame(protocol=ProtocolID.M_BUS_TUNNEL, service=0, body=b"")
            )
