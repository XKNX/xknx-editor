"""Block Bus Writes: reads pass, every frame that could change something on the bus is refused
at the interface, downloads are refused before they start, and the setting is kept, forced on by
``--no-bus-writes``, and can be turned on but not off by an MCP client."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx.cemi.cemi_frame import (
    CEMIFrame,
    CEMILData,
    CEMIMPropInfo,
    CEMIMPropReadRequest,
    CEMIMPropWriteRequest,
)
from xknx.cemi.const import CEMIMessageCode
from xknx.dpt import DPTBinary
from xknx.io.connection import ConnectionType
from xknx.io.knxip_interface import KNXIPInterface
from xknx.profile.const import ResourceDevicePropertyId, ResourceObjectType
from xknx.telegram import GroupAddress, IndividualAddress, apci, tpci

from editor_gui import main
from editor_gui import settings as app_settings
from editor_gui.plugins.connection import plugin as plugin_module
from editor_gui.plugins.connection.interface import ObservableKNXIPInterface
from editor_gui.plugins.connection.plugin import (
    _DEFAULT_CONTROLLER_IP,  # pyright: ignore[reportPrivateUsage]
    ConnectionPlugin,
)
from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.connection.write_guard import BusWritesBlocked, is_bus_write

_DEVICE = IndividualAddress("1.1.5")


def _frame(payload: apci.APCI | None, transport: tpci.TPCI | None = None) -> CEMIFrame:
    destination = (
        GroupAddress("1/2/3")
        if isinstance(
            payload,
            apci.GroupValueRead | apci.GroupValueWrite | apci.GroupValueResponse,
        )
        else _DEVICE
    )
    return CEMIFrame(
        code=CEMIMessageCode.L_DATA_REQ,
        data=CEMILData(
            src_addr=IndividualAddress("0.0.0"),
            dst_addr=destination,
            tpci=transport or tpci.TDataIndividual(),
            payload=payload,
        ),
    )


@pytest.mark.parametrize(
    "payload",
    [
        apci.GroupValueRead(),
        apci.MemoryRead(address=0x0100, count=4),
        apci.DeviceDescriptorRead(),
        apci.PropertyValueRead(object_index=0, property_id=11),
        apci.IndividualAddressRead(),
        apci.AuthorizeRequest(key=0xFFFFFFFF),
    ],
)
def test_reads_pass(payload: apci.APCI) -> None:
    assert not is_bus_write(_frame(payload))


@pytest.mark.parametrize(
    "payload",
    [
        apci.GroupValueWrite(DPTBinary(1)),
        apci.GroupValueResponse(DPTBinary(1)),
        apci.MemoryWrite(address=0x0100, data=b"\x00"),
        apci.Restart(),
        apci.IndividualAddressWrite(address=_DEVICE),
        apci.PropertyValueWrite(object_index=0, property_id=11, data=b"\x01"),
        apci.KeyWrite(level=0, key=0),
        # Encrypted (KNX Data Secure): what it carries cannot be seen.
        object.__new__(apci.SecureAPDU),
    ],
)
def test_writes_and_frames_it_cannot_look_into_are_writes(payload: Any) -> None:
    assert is_bus_write(_frame(payload))


def test_transport_control_passes() -> None:
    for control in (tpci.TConnect(), tpci.TDisconnect(), tpci.TAck(0), tpci.TNak(0)):
        assert not is_bus_write(_frame(None, control))


def test_local_property_reads_pass_and_writes_do_not() -> None:
    info = CEMIMPropInfo(
        object_type=ResourceObjectType.OBJECT_DEVICE,
        property_id=ResourceDevicePropertyId.PID_MAX_APDU_LENGTH,
    )
    read = CEMIFrame(
        code=CEMIMessageCode.M_PROP_READ_REQ,
        data=CEMIMPropReadRequest(property_info=info),
    )
    write = CEMIFrame(
        code=CEMIMessageCode.M_PROP_WRITE_REQ,
        data=CEMIMPropWriteRequest(property_info=info, data=b"\x00"),
    )
    assert not is_bus_write(read)
    assert is_bus_write(write)


def _interface(
    blocked: list[bool], monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, list[CEMIFrame]]:
    sent: list[CEMIFrame] = []

    async def _send(_self: Any, cemi: CEMIFrame) -> None:
        sent.append(cemi)

    monkeypatch.setattr(KNXIPInterface, "send_cemi", _send)
    interface = cast(Any, object.__new__(ObservableKNXIPInterface))
    interface._raw_cemi_callback = None
    interface._sent_cemi_callback = None
    interface._writes_blocked = lambda: blocked[0]
    return interface, sent


def test_the_interface_refuses_writes_only_while_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = [True]
    interface, sent = _interface(blocked, monkeypatch)
    write = _frame(apci.GroupValueWrite(DPTBinary(1)))
    asyncio.run(interface.send_cemi(_frame(apci.GroupValueRead())))
    with pytest.raises(BusWritesBlocked, match="Block Bus Writes is on"):
        asyncio.run(interface.send_cemi(write))
    assert len(sent) == 1
    blocked[0] = False
    asyncio.run(interface.send_cemi(write))
    assert len(sent) == 2


class _Log:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, event: str, **_kwargs: object) -> None:
        self.errors.append(event)


def test_a_download_is_refused_before_anything_is_sent() -> None:
    service = ConnectionService()
    log = _Log()
    service._log = cast(Any, log)  # pyright: ignore[reportPrivateUsage]
    service.block_bus_writes = True
    device = cast(Any, SimpleNamespace(individual_address="1.1.5", name="Actuator"))
    assert service.program_device(device) is None
    assert log.errors and "refused: Block Bus Writes is on" in log.errors[0]


def _plugin() -> Any:
    plugin = cast(Any, object.__new__(ConnectionPlugin))
    plugin._usb_target = None
    plugin._usb_addresses = {}
    plugin._controller_ip = _DEFAULT_CONTROLLER_IP
    plugin._multicast_group = "224.0.23.12"
    plugin._connection_type = ConnectionType.TUNNELING
    plugin._preferred_gateway_ip = None
    plugin._connect_at_startup = True
    plugin._block_bus_writes_setting = False
    plugin._block_bus_writes_forced = False
    plugin._log = SimpleNamespace(info=lambda *_a, **_k: None)
    plugin._api = SimpleNamespace(
        connection=SimpleNamespace(use_serial_number=True, block_bus_writes=False)
    )
    return plugin


def test_the_setting_is_kept_and_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin = _plugin()
    setting = {s.key: s for s in plugin.settings()}["block_bus_writes"]
    assert setting.get() is False
    setting.set(True)
    assert plugin._api.connection.block_bus_writes is True
    assert plugin_module.load_settings("connection")["block_bus_writes"] is True
    later = _plugin()
    ConnectionPlugin._load_saved_settings(later)  # pyright: ignore[reportPrivateUsage]
    assert later._api.connection.block_bus_writes is True


def test_mcp_may_turn_it_on_but_not_off() -> None:
    setting = {s.key: s for s in _plugin().settings()}["block_bus_writes"]
    assert setting.mcp_may_set(True)
    assert not setting.mcp_may_set(False)


def test_no_bus_writes_keeps_it_on_whatever_the_setting_says(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_settings, "config_dir", lambda: tmp_path)
    plugin = _plugin()
    plugin.block_bus_writes_this_start()
    setting = {s.key: s for s in plugin.settings()}["block_bus_writes"]
    assert plugin._api.connection.block_bus_writes is True
    assert setting.get() is True
    assert setting.read_only()
    # The stored setting is not changed by the command line.
    assert plugin._block_bus_writes_setting is False


def test_no_bus_writes_is_read_from_the_command_line() -> None:
    assert main.no_bus_writes(["editor", "--no-bus-writes"])
    assert not main.no_bus_writes(["editor", "Haus.xknx"])


# --- the telegram check before Data Secure, up-front refusals, the Group Monitor ---------------


class _Sent(Exception):
    """Raised by the fake interface after it took a frame, so no confirmation is awaited."""


def _handler(blocked: list[bool]) -> tuple[Any, list[CEMIFrame]]:
    """A CEMI handler with Block Bus Writes asked from ``blocked``, a Data Secure stand-in that
    turns every frame into one the interface cannot look into, and an interface that checks
    frames as the real one does."""
    from xknx import XKNX

    from editor_gui.cemi_diagnostics import DiagnosticCEMIHandler
    from editor_gui.plugins.connection.write_guard import check_frame

    xknx = XKNX()
    handler = DiagnosticCEMIHandler(
        xknx, mode="tunneling", target="test", writes_blocked=lambda: blocked[0]
    )
    sent: list[CEMIFrame] = []

    class _Encrypting:
        def outgoing_cemi(self, cemi_data: CEMILData) -> CEMILData:
            # Encrypted, a read looks like any other secure frame: unreadable to the guard.
            cemi_data.payload = apci.GroupValueWrite(DPTBinary(1))
            return cemi_data

    class _Interface:
        async def send_cemi(self, cemi: CEMIFrame) -> None:
            check_frame(lambda: blocked[0], cemi)
            sent.append(cemi)
            raise _Sent

    handler.data_secure = cast(Any, _Encrypting())
    xknx.knxip_interface = cast(Any, _Interface())
    return handler, sent


def test_a_secure_read_passes_as_it_is_checked_before_encryption() -> None:
    from xknx.telegram import Telegram

    handler, sent = _handler([True])
    read = Telegram(destination_address=_DEVICE, payload=apci.MemoryRead(0x4000, 4))
    with pytest.raises(_Sent):
        asyncio.run(handler.send_telegram(read))
    assert len(sent) == 1
    write = Telegram(
        destination_address=_DEVICE, payload=apci.MemoryWrite(0x4000, b"\x00")
    )
    with pytest.raises(BusWritesBlocked, match="MemoryWrite"):
        asyncio.run(handler.send_telegram(write))
    assert len(sent) == 1
    # A frame sent as it is, not through the handler, is still checked at the interface.
    from editor_gui.plugins.connection.write_guard import check_frame

    with pytest.raises(BusWritesBlocked):
        check_frame(lambda: True, _frame(apci.GroupValueWrite(DPTBinary(1))))


def test_without_the_setting_the_handler_sends_writes() -> None:
    from xknx.telegram import Telegram

    handler, sent = _handler([False])
    write = Telegram(
        destination_address=_DEVICE, payload=apci.MemoryWrite(0x4000, b"\x00")
    )
    with pytest.raises(_Sent):
        asyncio.run(handler.send_telegram(write))
    assert len(sent) == 1


def test_the_secure_sync_trigger_counts_as_the_read_it_is() -> None:
    from xknx.telegram import Telegram

    from editor_gui.plugins.connection.write_guard import is_bus_write_telegram
    from xknxeditor.download import secure_session

    trigger = secure_session._SyncTrigger()  # pyright: ignore[reportPrivateUsage]
    assert not is_bus_write_telegram(
        Telegram(destination_address=_DEVICE, payload=trigger)
    )


def _blocked_service() -> tuple[ConnectionService, _Log]:
    service = ConnectionService()
    log = _Log()
    service._log = cast(Any, log)  # pyright: ignore[reportPrivateUsage]
    service.block_bus_writes = True
    return service, log


def test_operations_that_write_are_refused_before_they_start() -> None:
    device = cast(Any, SimpleNamespace(individual_address="1.1.5", name="Actuator"))
    starts: dict[str, Any] = {
        "assign_individual_address": lambda s: s.assign_individual_address("1.1.5"),
        "assign_individual_address_by_serial": lambda s: (
            s.assign_individual_address_by_serial(bytes(6), "1.1.5")
        ),
        "restart_device": lambda s: s.restart_device(device),
        "master_reset_device": lambda s: s.master_reset_device(device, 0),
        "dali_commissioning": lambda s: s.run_dali(device, lambda _c: None),
        "siemens_dca_program": lambda s: s.run_siemens_dca_program(device, b"<x/>"),
    }
    for op, start in starts.items():
        service, log = _blocked_service()
        assert start(service) is None, op
        assert log.errors == [
            f"{op} refused: Block Bus Writes is on (Settings > Connection), nothing is "
            "written to the bus"
        ], op


def test_a_dali_scan_is_not_refused() -> None:
    service, log = _blocked_service()
    device = cast(Any, SimpleNamespace(individual_address="1.1.5", name="Gateway"))
    # Not connected here, so it does not start either, but not for Block Bus Writes.
    service.run_dali(device, lambda _c: None, writes=False)
    assert not any("Block Bus Writes" in error for error in log.errors)


def test_the_group_monitor_refuses_a_write_also_through_home_assistant() -> None:
    from xknx.telegram.apci import GroupValueRead, GroupValueWrite

    from editor_gui.plugins.monitor.service import MonitorService

    service, log = _blocked_service()
    monitor = MonitorService(service)
    elsewhere: list[tuple[str, object]] = []
    monitor.send_elsewhere = lambda address, payload: (
        elsewhere.append((address, payload)) or True
    )
    monitor.send_payload("1/2/3", DPTBinary(1))
    assert elsewhere == []
    assert log.errors and "group write refused" in log.errors[0]
    # A read still goes out through Home Assistant.
    monitor.send_read("1/2/3")
    assert [type(payload) for _, payload in elsewhere] == [GroupValueRead]
    assert GroupValueWrite not in [type(payload) for _, payload in elsewhere]
