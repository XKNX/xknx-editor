"""Management sessions for MDT custom symbol comparison and transfer."""

import contextlib
from dataclasses import dataclass

from xknx import XKNX
from xknx.exceptions import ManagementConnectionError, XKNXException
from xknx.telegram import IndividualAddress
from xknx.telegram.apci import (
    FunctionPropertyCommand,
    FunctionPropertyStateResponse,
    PropertyDescriptionRead,
    PropertyDescriptionResponse,
)

from editor_gui.plugins.connection.write_guard import mdt_firmware_info_read
from xknxeditor.download import DeviceProgrammer, DeviceSecurity, negotiated_apdu_length
from xknxeditor.download.errors import VerificationError
from xknxeditor.download.session import apdu_overhead, management_session
from xknxeditor.vendor.mdt import profile_for_application
from xknxeditor.vendor.mdt.firmware import FirmwareInfo, read_firmware
from xknxeditor.vendor.mdt.transfer import (
    compare_symbols,
    program_symbols,
    recover_symbols,
    synchronize_symbols,
    validate_symbols,
)


@dataclass(frozen=True)
class SymbolReadTarget:
    generation: int
    node_id: int
    address: str
    application_id: str
    entries: tuple[tuple[str, bytes], ...]


async def read_symbols(
    xknx: XKNX,
    address: str,
    security: DeviceSecurity | None,
    application_id: str,
    *,
    interface_max_apdu_length: int | None = None,
) -> dict[int, bytes]:
    """Read the complete library with an application check and a short-frame retry."""
    from xknxeditor.recover.identify import AppId, read_application_id

    profile = profile_for_application(application_id)
    if profile is None or profile.kind != "symbols":
        raise ValueError("Device has no MDT symbol configuration")
    manufacturer, program = application_id.split("_A-", 1)
    number, version, *_ = program.split("-")
    expected = AppId(manufacturer, int(number, 16), int(version, 16))
    for attempt in range(2):
        manager = management_session(xknx, IndividualAddress(address), security)
        try:
            connection = await manager.open()
            programmer = DeviceProgrammer(
                connection, apdu_overhead=apdu_overhead(security)
            )
            if attempt == 0:
                programmer.max_apdu_length = negotiated_apdu_length(
                    await programmer.read_max_apdu_length(), interface_max_apdu_length
                )
            if await read_application_id(programmer) != expected:
                raise ValueError("Device application does not match the project")
            return await recover_symbols(programmer, profile)
        except (ManagementConnectionError, XKNXException):
            if attempt:
                raise
        finally:
            with contextlib.suppress(Exception):
                await manager.close()
    raise RuntimeError("Symbol read did not complete")


async def run_symbols(
    xknx: XKNX,
    address: str,
    security: DeviceSecurity | None,
    application_id: str,
    symbols: dict[int, bytes],
    *,
    writes: bool,
    synchronize: bool = False,
) -> dict[int, bool] | int:
    profile = profile_for_application(application_id)
    if profile is None:
        raise ValueError("Device has no MDT symbol configuration")
    if writes and synchronize:
        raise ValueError("Cannot program and synchronize in the same operation")
    validate_symbols(profile, symbols, writes=writes)
    if synchronize and set(symbols) != set(range(profile.slots)):
        raise ValueError("Synchronization requires the complete symbol library")
    manager = management_session(xknx, IndividualAddress(address), security)
    connection = await manager.open()
    try:
        programmer = DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security))
        programmer.max_apdu_length = min(55, await programmer.read_max_apdu_length())
        if synchronize:
            return await synchronize_symbols(programmer, profile, symbols)
        if writes:
            return await program_symbols(programmer, profile, symbols)
        return await compare_symbols(programmer, profile, symbols)
    finally:
        with contextlib.suppress(Exception):
            await manager.close()


@dataclass(frozen=True)
class FirmwareReadTarget:
    generation: int
    node_id: int
    address: str
    application_id: str
    product: str


class _FirmwareReader:
    def __init__(
        self, programmer: DeviceProgrammer, address: IndividualAddress
    ) -> None:
        self.programmer = programmer
        self.address = address
        self.manufacturer_verified = False
        self.property_verified = False

    async def read_property(self, object_index: int, property_id: int) -> bytes:
        data = await self.programmer.read_property(object_index, property_id)
        if (object_index, property_id) == (0, 12):
            self.manufacturer_verified = data == b"\x00\x83"
        return data

    async def read_bsl_info_packet(self, sequence: int) -> bytes:
        if not self.manufacturer_verified:
            raise ValueError("MDT manufacturer has not been verified")
        connection = self.programmer.connection
        if not self.property_verified:
            telegram = await connection.request(
                PropertyDescriptionRead(object_index=0, property_id=242),
                PropertyDescriptionResponse,
            )
            description = telegram.payload
            if (
                not isinstance(description, PropertyDescriptionResponse)
                or description.object_index != 0
                or description.property_id != 242
                or description.max_count == 0
            ):
                raise VerificationError("MDT firmware identity property is unavailable")
            self.property_verified = True
        with mdt_firmware_info_read(self.address, sequence):
            telegram = await connection.request(
                FunctionPropertyCommand(
                    object_index=0, property_id=242, data=bytes((0, 0x11, sequence))
                ),
                FunctionPropertyStateResponse,
            )
        response = telegram.payload
        if (
            not isinstance(response, FunctionPropertyStateResponse)
            or response.object_index != 0
            or response.property_id != 242
        ):
            raise VerificationError("Invalid MDT firmware identity response")
        return response.data


async def read_firmware_version(
    xknx: XKNX,
    address: str,
    security: DeviceSecurity | None,
) -> FirmwareInfo:
    """Read version and hardware/software identity without starting an update."""
    manager = management_session(xknx, IndividualAddress(address), security)
    try:
        connection = await manager.open()
        return await read_firmware(
            _FirmwareReader(
                DeviceProgrammer(connection, apdu_overhead=apdu_overhead(security)),
                IndividualAddress(address),
            )
        )
    finally:
        with contextlib.suppress(Exception):
            await manager.close()
