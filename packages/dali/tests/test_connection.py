"""Transport tests for MdtDaliConnection: command framing, poll state machine, table i/o."""

import pytest
from _dali_fakes import FakeProgrammer

from xknxeditor.dali.connection import DaliCommissioningError, MdtDaliConnection
from xknxeditor.dali.properties import DeviceCommand, PropertyType, object_index


def _conn(channel: int = 0) -> tuple[MdtDaliConnection, FakeProgrammer]:
    prog = FakeProgrammer()
    return MdtDaliConnection(prog, channel), prog


async def test_object_index_per_channel() -> None:
    assert object_index(0) == 6 and object_index(1) == 7


async def test_send_command_frames_three_bytes_on_object_6() -> None:
    conn, prog = _conn()
    await conn.send_command(DeviceCommand.PLUGIN_SYNC, 1, 2)
    (w,) = prog.writes_for(PropertyType.COMMAND)
    assert w["object_index"] == 6
    assert w["data"] == bytes((24, 1, 2))
    assert w["start_index"] == 1 and w["count"] == 1


async def test_poll_done_and_error_and_timeout() -> None:
    conn, prog = _conn()
    # running, running, done(0)
    prog.queue_read(
        PropertyType.COMMAND, bytes((1, 3, 0)), bytes((1, 5, 0)), bytes((0, 8, 0))
    )
    result = await conn.poll_result(timeout=5.0, interval=0.0)
    assert result.cmd == 0 and result.param1 == 8

    conn2, prog2 = _conn()
    prog2.queue_read(PropertyType.COMMAND, bytes((1, 0, 0)), bytes((0xFF, 0, 0)))
    with pytest.raises(DaliCommissioningError, match="error"):
        await conn2.poll_result(timeout=5.0, interval=0.0)

    conn3, prog3 = _conn()
    prog3.set_read(PropertyType.COMMAND, bytes((1, 0, 0)))  # never finishes
    with pytest.raises(DaliCommissioningError, match="timed out"):
        await conn3.poll_result(timeout=0.0, interval=0.0)


async def test_read_table_chunks_until_all_elements() -> None:
    conn, prog = _conn()
    # A short response advances by the actual element count.
    prog.queue_read(
        PropertyType.EVG_PROP,
        *([b"\x01\x00\x20\x00" * 8] * 4),
        *([b"\x02\x00\x05\x01" * 8] * 4),
    )
    data = await conn.read_table(PropertyType.EVG_PROP, 64)
    assert len(data) == 64 * 4
    assert data[:4] == b"\x01\x00\x20\x00" and data[128:132] == b"\x02\x00\x05\x01"


async def test_table_size_read_write() -> None:
    conn, prog = _conn()
    prog.set_read(PropertyType.SCENE_KNX_MAP, bytes((0x00, 0x10)))
    assert await conn.read_table_size(PropertyType.SCENE_KNX_MAP) == 16
    await conn.write_table_size(PropertyType.SCENE_KNX_MAP, 16)
    (w,) = prog.writes_for(PropertyType.SCENE_KNX_MAP)
    assert w["data"] == bytes((0x00, 0x10)) and w["start_index"] == 0


@pytest.mark.parametrize("data", [b"", bytes(5), bytes(4 * 16)])
async def test_read_table_rejects_empty_partial_and_oversized_responses(data):
    conn, prog = _conn()
    prog.queue_read(PropertyType.EVG_PROP, data)
    with pytest.raises(DaliCommissioningError):
        await conn.read_table(PropertyType.EVG_PROP, 15)


async def test_terminated_table_stops_without_requesting_missing_trailing_slots():
    conn, prog = _conn()
    first = bytes([1, 2, 3, 4, 5, 6, 7, 8])
    prog.queue_read(PropertyType.SCENE_GROUP_VALUE, first, b"\xff" * 8)
    assert (
        await conn.read_table(
            PropertyType.SCENE_GROUP_VALUE, 1024, terminator=b"\xff" * 8
        )
        == first + b"\xff" * 8
    )
    assert prog.reads == [(214, 1, 1), (214, 2, 1)]


async def test_unterminated_short_table_raises():
    conn, prog = _conn()
    prog.queue_read(PropertyType.SCENE_GROUP_VALUE, bytes(8))
    with pytest.raises(DaliCommissioningError, match="short read"):
        await conn.read_table(
            PropertyType.SCENE_GROUP_VALUE, 1024, terminator=b"\xff" * 8
        )


@pytest.mark.parametrize("data", [b"\x00", bytes(3)])
async def test_malformed_descriptor_is_not_an_absent_property(data):
    conn, prog = _conn()
    prog.queue_read(PropertyType.SCENE_KNX_MAP, data)
    with pytest.raises(DaliCommissioningError):
        await conn.read_table_size(PropertyType.SCENE_KNX_MAP)


@pytest.mark.parametrize(
    "payload_bytes,expected_requests", [(10, [(1, 1), (2, 1)]), (30, [(1, 2)])]
)
async def test_reads_fit_negotiated_response_budget(payload_bytes, expected_requests):
    conn, prog = _conn()
    prog.max_payload_bytes = payload_bytes
    prog.set_read(PropertyType.EVG_STATIC, bytes(range(16)))
    assert await conn.read_table(PropertyType.EVG_STATIC, 2) == bytes(range(16))
    assert [(start, count) for _, start, count in prog.reads] == expected_requests


async def test_read_cannot_fit_element_fails_without_request():
    conn, prog = _conn()
    prog.max_payload_bytes = 7
    with pytest.raises(DaliCommissioningError, match="APDU"):
        await conn.read_table(PropertyType.EVG_STATIC, 2)
    assert not prog.reads
