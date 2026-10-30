"""What an unload does around its procedure: the programming mode, the additional individual
addresses, the serial number and, for an unload of everything, the address."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from xknx import XKNX
from xknx.telegram import apci
from xknx.telegram.address import IndividualAddress
from xknx.telegram.apci import PropertyValueRead, PropertyValueResponse

import xknxeditor.download.unload as unload
from xknxeditor.download.errors import ProgrammingModeError
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.resources import MaskResources
from xknxeditor.namespaces.intermediate.resource_addr_space_t import ResourceAddrSpace
from xknxeditor.namespaces.intermediate.resource_location_t import ResourceLocation
from xknxeditor.namespaces.intermediate.resource_name_t import ResourceName

from .conftest import FakeDevice


def _property(index: int, pid: int) -> ResourceLocation:
    return ResourceLocation(
        address_space=ResourceAddrSpace.SYSTEM_PROPERTY,
        interface_object_ref=index,
        property_id=pid,
        start_address=0,
    )


_RESOURCES = MaskResources(
    _by_name={
        ResourceName.DEVICE_SERIAL_NUMBER: _property(0, 11),
        ResourceName.PROGRAMMING_MODE: _property(0, 54),
    }
)
_SERIAL = bytes.fromhex("00FA12345678")


class _IpDevice(FakeDevice):
    """A device with a KNXnet/IP parameter object (index 1) and two tunnel addresses."""

    def __init__(self, tunnels: list[str]) -> None:
        super().__init__({0: 0, 1: 11}, 0x07B0)
        self.properties[1, 53] = b"".join(
            IndividualAddress(t).raw.to_bytes(2, "big") for t in tunnels
        )
        self.property_element_sizes[1, 53] = 2
        self.properties[0, 11] = _SERIAL

    def _handle_property_read(
        self, payload: PropertyValueRead
    ) -> PropertyValueResponse:
        if (payload.object_index, payload.property_id) == (1, 53) and (
            payload.start_index == 0
        ):
            count = len(self.properties[1, 53]) // 2
            return PropertyValueResponse(
                object_index=1,
                property_id=53,
                data=count.to_bytes(2, "big"),
                count=1,
                start_index=0,
            )
        return super()._handle_property_read(payload)


def _progmode(device: FakeDevice) -> bytes | None:
    return device.properties.get((0, 54))


async def _on_connection(device: FakeDevice, **kwargs: Any) -> bytes | None:
    options: dict[str, Any] = {
        "unload_address": True,
        "in_programming_mode": False,
        "restarted": False,
        "own_address": IndividualAddress("1.0.255"),
        **kwargs,
    }
    return await unload.on_device_connection(
        DeviceProgrammer(device), _RESOURCES, **options
    )


async def test_an_unload_of_everything_reads_the_serial_number() -> None:
    device = _IpDevice(["1.1.200", "1.1.201"])
    assert await _on_connection(device) == _SERIAL
    # The tunnel addresses are unset, the programming mode is switched off (not needed).
    assert device.properties[1, 53] == b"\xff\xff" * 2
    assert _progmode(device) == b"\x00"


async def test_without_a_serial_number_the_programming_mode_is_switched_on() -> None:
    device = _IpDevice([])
    device.properties[0, 11] = bytes(6)
    assert await _on_connection(device) is None
    assert _progmode(device) == b"\x01"
    # Unless the user put the device into programming mode.
    device = _IpDevice([])
    device.properties[0, 11] = bytes(6)
    assert await _on_connection(device, in_programming_mode=True) is None
    assert _progmode(device) is None


async def test_the_tunnel_in_use_stays() -> None:
    device = _IpDevice(["1.1.200", "1.0.255"])
    await _on_connection(device)
    assert device.properties[1, 53] == bytes.fromhex("11C810FF")


async def test_an_unload_of_the_application_switches_the_programming_mode_off() -> None:
    device = _IpDevice(["1.1.200"])
    assert await _on_connection(device, unload_address=False) is None
    assert _progmode(device) == b"\x00"
    assert device.properties[1, 53] == bytes.fromhex("11C8")
    # Not after a restart.
    device = _IpDevice([])
    await _on_connection(device, unload_address=False, restarted=True)
    assert _progmode(device) is None


def test_suspicious_serial_numbers(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not unload.suspicious_serial_number(_SERIAL)
    assert unload.suspicious_serial_number(bytes(6))
    assert unload.suspicious_serial_number(bytes.fromhex("00FAFFFFFFFF"))
    # A text manufacturer part, or a placeholder one.
    assert unload.suspicious_serial_number(b"11" + bytes.fromhex("12345678"))
    assert unload.suspicious_serial_number(bytes.fromhex("0300 00123456"))
    monkeypatch.setenv("XKNX_NO_SERIAL_NUMBER_CHECK", "1")
    assert not unload.suspicious_serial_number(bytes(6))


def test_the_unloaded_address_of_the_mask() -> None:
    feature = SimpleNamespace(
        name=SimpleNamespace(value="UnloadedIndividualAddress"), value=0xFF00
    )
    master = cast(
        Any,
        SimpleNamespace(
            mask_versions=SimpleNamespace(
                mask_version=[
                    SimpleNamespace(
                        id="MV-07B0",
                        hawk_configuration_data=[
                            SimpleNamespace(features=SimpleNamespace(feature=[feature]))
                        ],
                    )
                ]
            )
        ),
    )
    assert unload.unloaded_address(master, "MV-07B0") == IndividualAddress("15.15.0")
    assert unload.unloaded_address(master, "MV-0705") == IndividualAddress("15.15.255")
    assert unload.unloaded_address(None, "MV-07B0") == IndividualAddress("15.15.255")


class _FakeConnection:
    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakeXKNX:
    def __init__(self, calls: list[tuple[str, object]]) -> None:
        self.calls = calls
        self.management = SimpleNamespace(
            broadcast=SimpleNamespace(send=self._send), connection=self._connect
        )

    async def _send(self, payload: object) -> None:
        self.calls.append(("broadcast", payload))

    def _connect(self, address: object) -> _FakeConnection:
        self.calls.append(("connect", address))
        return _FakeConnection()


async def _write(
    monkeypatch: pytest.MonkeyPatch,
    in_programming_mode: list[list[IndividualAddress]],
    serial: bytes | None = None,
) -> list[tuple[str, object]]:
    calls: list[tuple[str, object]] = []

    async def read(xknx: XKNX, raise_if_multiple: bool = False) -> list[Any]:
        calls.append(("read", raise_if_multiple))
        return in_programming_mode.pop(0)

    async def restart(connection: object) -> None:
        calls.append(("restart", connection))

    async def serial_write(xknx: XKNX, serial: bytes, address: object) -> None:
        calls.append(("serial", (serial, address)))

    monkeypatch.setattr(unload, "nm_individual_address_read", read)
    monkeypatch.setattr(unload, "dm_restart_r_co", restart)
    monkeypatch.setattr(
        unload, "nm_individual_address_serial_number_write", serial_write
    )
    monkeypatch.setattr(unload, "_ADDRESS_WRITE_DELAY_S", 0)
    await unload.write_unloaded_address(
        cast("XKNX", _FakeXKNX(calls)), IndividualAddress("15.15.255"), serial
    )
    return calls


async def test_the_address_by_serial_number(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = await _write(monkeypatch, [], _SERIAL)
    assert calls == [("serial", (_SERIAL, IndividualAddress("15.15.255")))]


async def test_the_address_by_programming_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = await _write(monkeypatch, [[IndividualAddress("1.1.24")], []])
    assert [c[0] for c in calls] == ["read", "broadcast", "connect", "restart", "read"]
    payload = next(c[1] for c in calls if c[0] == "broadcast")
    assert isinstance(payload, apci.IndividualAddressWrite)
    assert payload.address == IndividualAddress("15.15.255")


async def test_a_device_that_stays_in_programming_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stays = [IndividualAddress("15.15.255")]
    with pytest.raises(ProgrammingModeError, match="still in programming mode"):
        await _write(monkeypatch, [[IndividualAddress("1.1.24")], *[stays] * 4])
    with pytest.raises(ProgrammingModeError, match="No device is in programming mode"):
        await _write(monkeypatch, [[]])


class _LockedDevice(_IpDevice):
    """A device locked with the project's key: it grants level 1 and takes key writes."""

    def __init__(self) -> None:
        super().__init__([])
        self.authorize_level = 1
        self.key_writes: list[tuple[int, int]] = []

    async def request(self, payload: Any, expected: Any) -> Any:
        if isinstance(payload, apci.KeyWrite):
            self.key_writes.append((payload.level, payload.key))
            return self._telegram(apci.KeyResponse(level=payload.level))
        return await super().request(payload, expected)


async def test_an_unload_removes_the_projects_access_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib

    dl = importlib.import_module("xknxeditor.download.download")
    device = _LockedDevice()

    class Manager:
        async def open(self) -> _LockedDevice:
            return device

        async def close(self) -> None:
            pass

    monkeypatch.setattr(dl, "_connection_manager", lambda *args: Manager())
    monkeypatch.setattr(dl, "_authorize_levels", lambda *args: 3)
    application = cast(
        Any, SimpleNamespace(program=SimpleNamespace(mask_version="MV-07B0"))
    )
    await dl._after_unload_procedure(  # pyright: ignore[reportPrivateUsage]
        cast("XKNX", SimpleNamespace(current_address=None)),
        IndividualAddress("1.1.5"),
        None,
        application,
        None,
        0x12345678,
        unload_address=False,
        in_programming_mode=False,
        restarted=True,
    )
    assert device.authorize_keys == [0x12345678]
    # Every level from the granted one on gets the free access key again.
    assert device.key_writes == [(1, 0xFFFFFFFF), (2, 0xFFFFFFFF)]


@pytest.mark.parametrize(
    ("serial", "suspicious"),
    [
        # Serial numbers devices of their manufacturer are known to share: by range, by
        # value, from a list and by a mask.
        ("000201362FC6", True),
        ("000228052017", True),
        ("000A257D2B11", True),
        ("007600FFFFFF", True),
        # Real ones of the same manufacturers.
        ("000200000009", False),
        ("000774148BF8", False),
        ("000A00000001", False),
    ],
)
def test_serial_numbers_devices_share(serial: str, suspicious: bool) -> None:
    assert unload.suspicious_serial_number(bytes.fromhex(serial)) is suspicious
