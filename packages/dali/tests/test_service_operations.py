import struct

import pytest
from _dali_fakes import FakeProgrammer

from xknxeditor.dali.connection import DaliCommissioningError, MdtDaliConnection
from xknxeditor.dali.profiles import PROFILES
from xknxeditor.dali.properties import PropertyType
from xknxeditor.dali.service_operations import (
    emergency_test,
    encode_names,
    maintenance,
    memory_bank,
    read_calibration,
    read_motion,
    read_names,
    save_calibration,
    set_calibration_level,
    write_names,
)


@pytest.fixture
def connection(monkeypatch):
    async def no_delay(_):
        pass

    monkeypatch.setattr("xknxeditor.dali.service_operations.asyncio.sleep", no_delay)
    programmer = FakeProgrammer()
    return MdtDaliConnection(programmer, profile=PROFILES[2]), programmer


@pytest.mark.parametrize(
    "kind,command", [("function", 20), ("duration", 22), ("battery", 23)]
)
async def test_emergency_checks_type_and_pending_status(connection, kind, command):
    c, p = connection
    p.set_read(PropertyType.EVG_PROP, bytes([129, 0, 16, 7]))
    p.set_read(PropertyType.CONVERTER_STATUS, bytes(5))
    await emergency_test(c, 0, kind)
    assert p.writes[-1]["data"] == bytes([command, 0, 0])
    p.set_read(PropertyType.EVG_PROP, bytes([8, 0, 16, 7]))
    with pytest.raises(DaliCommissioningError, match="converter"):
        await emergency_test(c, 0, kind)
    p.set_read(PropertyType.EVG_PROP, bytes([1, 0, 16, 7]))
    if kind != "battery":
        for status in (7, 8, 64, 128):
            p.set_read(PropertyType.CONVERTER_STATUS, bytes([status, 0, 0, 0, 0]))
            with pytest.raises(DaliCommissioningError, match="already"):
                await emergency_test(c, 0, kind)
    assert len(p.writes) == 1


async def test_calibration_encoding_bounds_and_verification(connection):
    c, p = connection
    p.set_read(
        PropertyType.CONSTANT_LIGHT, struct.pack(">BHHBHH", 51, 200, 100, 255, 600, 800)
    )
    actual = await read_calibration(c, 0)
    assert (actual.minimum_level, actual.minimum_lux, actual.maximum_sensor) == (
        20,
        200,
        800,
    )
    await set_calibration_level(c, 0, 20)
    assert p.writes[-1]["data"] == bytes([9, 0, 51])
    await save_calibration(c, 0, 20, 200)
    assert p.writes[-1]["data"] == bytes.fromhex("303300c800030000")
    result = await maintenance(c, "save_calibration", 0, 20, 200, 2, False, "mds", None)
    assert result["minimum_lux"] == 200
    with pytest.raises(DaliCommissioningError, match="readback"):
        await maintenance(c, "save_calibration", 0, 20, 201, 2, False, "mds", None)
    before = len(p.writes)
    with pytest.raises(DaliCommissioningError):
        await set_calibration_level(c, 0, 30)
    assert len(p.writes) == before


async def test_memory_wire_bytes_error_and_write_verification(connection):
    c, p = connection
    p.queue_read(
        PropertyType.EXT_COMMAND,
        bytes([34, 0, 0, 0, 0, 0, 0, 0]),
        bytes([0, 0, 0, 0, 42, 0, 0, 0]),
    )
    assert await memory_bank(c, 7, 199, 254) == 42
    assert p.writes[-1]["data"] == bytes([34, 199, 254, 128, 0, 3, 7, 0])
    p.queue_read(PropertyType.EXT_COMMAND, bytes([255, 0, 0, 0, 0, 0, 0, 0]))
    with pytest.raises(DaliCommissioningError, match="failed"):
        await memory_bank(c, 0, 2, 0)
    p.set_read(PropertyType.EXT_COMMAND, bytes([0, 0, 0, 0, 42, 0, 0, 0]))
    result = await maintenance(c, "write_memory", 7, 42, 254, 199, True, "mds", None)
    assert result["value"] == 42
    assert p.writes[-2]["data"] == bytes([33, 199, 254, 1, 42, 3, 7, 0])
    with pytest.raises(DaliCommissioningError, match="readback"):
        await maintenance(c, "write_memory", 7, 41, 254, 199, False, "mds", None)


async def test_names_utf8_exact_width_and_readback(connection):
    c, p = connection
    names = ["ä" * 10, "Kitchen"] + [""] * 14
    data = encode_names("groups", names)
    p.set_read(PropertyType.GROUP_NAME, data)
    assert await read_names(c, "groups") == names
    await write_names(c, "groups", names)
    assert p.writes[-1]["count"] == 32
    with pytest.raises(DaliCommissioningError):
        encode_names("groups", ["ä" * 11, *names[1:]])
    with pytest.raises(DaliCommissioningError):
        encode_names("groups", ["a\0b", *names[1:]])
    with pytest.raises(DaliCommissioningError, match="readback"):
        await write_names(c, "groups", ["Other", *names[1:]])


async def test_motion_diagnostic_endianness(connection):
    c, p = connection
    p.set_read(PropertyType.MOTION_INPUT, bytes.fromhex("21013412030708cdab80") * 8)
    states = await read_motion(c)
    assert len(states) == 8
    assert (
        states[7].index,
        states[7].kind,
        states[7].error,
        states[7].brightness,
        states[7].setpoint,
    ) == (7, 1, 32, 0x1234, 0xABCD)


@pytest.mark.parametrize(
    "action,index,value,secondary,bank,section",
    [
        ("function", 64, 0, 0, 2, "ecgs"),
        ("write_memory", 0, 256, 0, 2, "mds"),
        ("read_memory", 0, 0, 256, 2, "mds"),
        ("read_names", 0, 0, 0, 2, "inputs"),
        ("save_calibration", 0, 10, -1, 2, "mds"),
        ("unknown", 0, 0, 0, 2, "ecgs"),
    ],
)
async def test_invalid_service_request_never_reaches_transport(
    connection, action, index, value, secondary, bank, section
):
    c, p = connection
    with pytest.raises(DaliCommissioningError):
        await maintenance(
            c, action, index, value, secondary, bank, False, section, None
        )
    assert p.writes == [] and p.reads == []
