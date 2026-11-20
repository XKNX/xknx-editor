"""Discovery and address changes verify the physical identity and command results."""

from __future__ import annotations

import asyncio

import pytest

from xknxeditor.dali.connection import DaliCommissioningError
from xknxeditor.dali.siemens_dali2 import SiemensDali2Commissioner, decode_scan


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes((value,)))


def uint(field, value):
    return varint(field << 3) + varint(value)


def nested(field, value):
    return varint((field << 3) | 2) + varint(len(value)) + value


def inventory(devices=((7, 0x123456, 0),), *, sensors=False):
    data = uint(1, 17 if sensors else 1)
    for address, random, flags in devices:
        row = uint(1, flags) + uint(2, address) + uint(3, random) + uint(4, 2)
        if sensors:
            row += nested(6, uint(1, 3) + uint(2, 4) + uint(3, 12))
        else:
            row += nested(5, b"\x06\x08") + uint(6, 26)
        data += nested(2, row)
    return data


class Programmer:
    def __init__(self):
        self.calls = []
        self.polling = 1
        self.ecgs = {7: 0x123456}
        self.sensors = {7: 0x123456}
        self.images = {}
        self.states = {}
        self.override = None
        self.memory_override = None
        self.pointer_override = None
        self.selected = 0
        self.dtr = 0
        self.scans = 0

    async def read_property(self, object_index, property_id, **kwargs):
        self.calls.append(("property", object_index, property_id))
        if property_id == 54:
            return bytes((self.polling,))
        if self.pointer_override is not None:
            return self.pointer_override
        pointer = 0x1000 if property_id == 55 else 0x2000
        self.images[pointer] = inventory(
            tuple(
                (a, r, 0)
                for a, r in (self.ecgs if property_id == 55 else self.sensors).items()
            ),
            sensors=property_id == 56,
        )
        return pointer.to_bytes(4, "big")

    async def read_user_memory(self, address, size):
        self.calls.append(("memory", address, size))
        if self.memory_override is not None:
            return self.memory_override
        if address in self.images:
            return len(self.images[address]).to_bytes(4, "little")
        return self.images[address - 4]

    async def function_property_raw(self, object_index, property_id, data, *, command):
        self.calls.append(
            ("command" if command else "state", object_index, property_id, data)
        )
        if self.override is not None:
            value = await self.override(data, command)
            if value is not None:
                return value
        service = data[1]
        if not command:
            state = self.states.get(service, 0)
            result = bytes((0, service, state))
            return result + bytes(6) + b"\x01" if service == 65 else result
        if service == 80:
            self.polling = data[2]
            return b"\x00"
        if service == 96:
            return b"\x00"
        if service in (17, 33):
            self.scans += 1
            self.states[service] = 2
            return bytes((0, service, 4))
        assert service == 65
        self.states[service] = 2
        bits = data[8]
        if bits == 16:
            first, second = data[6:8]
            if first == 163:
                self.dtr = second
            elif first & 1 and second == 128 and first < 128:
                self.ecgs[255] = self.ecgs.pop(first >> 1)
            elif first in (177, 179, 181):
                shift = {177: 16, 179: 8, 181: 0}[first]
                self.selected = (self.selected & ~(255 << shift)) | second << shift
            elif first == 183:
                assert self.ecgs[255] == self.selected
                self.ecgs[second >> 1] = self.ecgs.pop(255)
        else:
            first, second, third = data[5:8]
            if first < 128 and second == 254 and third == 20:
                self.sensors[255] = self.sensors.pop(first >> 1)
            elif first == 193 and second in (5, 6, 7):
                shift = {5: 16, 6: 8, 7: 0}[second]
                self.selected = (self.selected & ~(255 << shift)) | third << shift
            elif first == 193 and second == 8:
                assert self.sensors[255] == self.selected
                self.sensors[third] = self.sensors.pop(255)
        return bytes((0, 65, 4))


@pytest.mark.parametrize("sensors", [False, True])
async def test_cached_inventory_uses_user_memory_without_commands(sensors):
    programmer = Programmer()
    commissioner = SiemensDali2Commissioner(programmer, 1)
    scan = await commissioner.read_cached(sensors=sensors)
    assert scan.line == 1 and scan.sensors == sensors
    device = scan.devices[0]
    assert (device.short_address, device.random_address, device.version) == (
        7,
        0x123456,
        2,
    )
    if sensors:
        assert device.instances[0].type == 4
        assert device.instances[0].resolution == 12
    else:
        assert device.types == (6, 8) and device.min_level == 26
    assert not any(call[0] in ("command", "state") for call in programmer.calls)
    assert all(call[1] == 17 for call in programmer.calls if call[0] == "property")


@pytest.mark.parametrize("sensors", [False, True])
async def test_fresh_discovery_waits_for_success_and_restores_polling(sensors):
    programmer = Programmer()
    scan = await SiemensDali2Commissioner(programmer, poll_interval=0.001).discover(
        sensors=sensors
    )
    assert len(scan.devices) == 1 and programmer.polling == 1
    commands = [c[-1] for c in programmer.calls if c[0] == "command"]
    assert commands == [
        b"\x00\x50\x00",
        bytes((0, 33 if sensors else 17, 0)),
        b"\x00\x50\x01",
    ]
    assert any(
        c[0] == "state" and c[-1] == bytes((0, 33 if sensors else 17))
        for c in programmer.calls
    )


@pytest.mark.parametrize(
    "response",
    [
        b"",
        b"\xff",
        b"\x00\x12\x02",
        b"\x00\x11\x03",
        b"\x00\x11\x05",
        b"\x00\x11\xff",
    ],
)
async def test_rejected_or_malformed_scan_is_never_reported_successful(response):
    programmer = Programmer()

    async def override(data, command):
        return response if command and data[1] == 17 else None

    programmer.override = override
    with pytest.raises(DaliCommissioningError):
        await SiemensDali2Commissioner(programmer).discover()
    assert programmer.polling == 1
    assert not any(c[0] == "memory" for c in programmer.calls)


async def test_timeout_during_scan_restores_polling():
    programmer = Programmer()
    started = False

    async def override(data, command):
        nonlocal started
        if data[1] == 17:
            if command:
                started = True
            return bytes((0, 17, 1 if started else 0))
        return None

    programmer.override = override
    with pytest.raises(TimeoutError):
        await SiemensDali2Commissioner(programmer, poll_interval=0.001).discover(
            timeout=0.02
        )
    assert programmer.polling == 1


async def test_cancellation_during_scan_restores_polling():
    programmer = Programmer()
    started = asyncio.Event()

    async def override(data, command):
        if data[1] == 17 and command:
            started.set()
            await asyncio.Future()
        return None

    programmer.override = override
    task = asyncio.create_task(SiemensDali2Commissioner(programmer).discover())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert programmer.polling == 1


async def test_existing_polling_pause_is_not_resumed():
    programmer = Programmer()
    programmer.polling = 0
    await SiemensDali2Commissioner(programmer).identify(7)
    assert programmer.polling == 0


@pytest.mark.parametrize(
    "sensors,start,payload",
    [
        (False, True, "006010070014"),
        (True, True, "006012070014"),
        (False, False, "0060110700"),
        (True, False, "0060110700"),
    ],
)
async def test_identification_command_matches_device_kind(sensors, start, payload):
    programmer = Programmer()
    await SiemensDali2Commissioner(programmer).identify(7, sensors=sensors, start=start)
    assert ("command", 16, 52, bytes.fromhex(payload)) in programmer.calls
    assert programmer.polling == 1


@pytest.mark.parametrize("sensors", [False, True])
async def test_move_address_checks_identity_and_verifies_fresh_inventory(sensors):
    programmer = Programmer()
    result = await SiemensDali2Commissioner(
        programmer, poll_interval=0.001
    ).change_short_address(7, 12, 0x123456, sensors=sensors)
    assert [(d.short_address, d.random_address) for d in result.devices] == [
        (12, 0x123456)
    ]
    assert programmer.scans == 2 and programmer.polling == 1
    frames = [c[-1] for c in programmer.calls if c[0] == "command" and c[-1][1] == 65]
    assert len(frames) == 8
    assert frames[-1] == bytes.fromhex(
        "0041020300c100001801" if sensors else "004102030000a1001001"
    )
    assert frames[2] == bytes.fromhex(
        "0041050300c101ff1801" if sensors else "004105030000a5001001"
    )


@pytest.mark.parametrize(
    "devices,random",
    [
        ({7: 0x111111}, 0x123456),
        ({7: 0x123456, 12: 9}, 0x123456),
        ({7: 0x123456, 8: 0x123456}, 0x123456),
    ],
)
async def test_move_refuses_changed_or_ambiguous_identity_before_dali_frames(
    devices, random
):
    programmer = Programmer()
    programmer.ecgs = devices
    with pytest.raises(DaliCommissioningError):
        await SiemensDali2Commissioner(
            programmer, poll_interval=0.001
        ).change_short_address(7, 12, random)
    assert not any(c[0] == "command" and c[-1][1] == 65 for c in programmer.calls)


async def test_failed_frame_terminates_addressing_and_resumes_polling():
    programmer = Programmer()

    async def override(data, command):
        if command and data[1] == 65 and data[6] == 177:
            return b"\xff"
        return None

    programmer.override = override
    with pytest.raises(DaliCommissioningError):
        await SiemensDali2Commissioner(
            programmer, poll_interval=0.001
        ).change_short_address(7, 12, 0x123456)
    assert programmer.polling == 1
    frames = [c[-1] for c in programmer.calls if c[0] == "command" and c[-1][1] == 65]
    assert frames[-1] == bytes.fromhex("004102030000a1001001")
    assert not any(frame[6] == 183 for frame in frames)


@pytest.mark.parametrize(
    "pointer,memory",
    [
        (b"", None),
        (b"\x00\x10\x00\x00", None),
        (b"\x00\x00\x10\x00", b"\x00"),
        (b"\x00\x00\x10\x00", b"\xff" * 4),
    ],
)
async def test_invalid_image_bounds_fail(pointer, memory):
    programmer = Programmer()
    programmer.pointer_override = pointer
    programmer.memory_override = memory
    with pytest.raises(DaliCommissioningError):
        await SiemensDali2Commissioner(programmer).read_cached()


async def test_missing_cache_is_distinct_from_empty_scan():
    programmer = Programmer()
    programmer.pointer_override = bytes(4)
    assert await SiemensDali2Commissioner(programmer).read_cached() is None
    programmer.pointer_override = None
    programmer.ecgs = {}
    assert (await SiemensDali2Commissioner(programmer).read_cached()).devices == ()


@pytest.mark.parametrize(
    "data",
    [
        b"\x00",
        b"\x12\x05\x01",
        b"\x18\x02",
        b"\x12\x02\x10\x40",
        b"\x08" + b"\xff" * 10,
        inventory(((1, 3, 0), (1, 4, 0))),
    ],
)
def test_invalid_or_partial_protobuf_rejected(data):
    with pytest.raises(DaliCommissioningError):
        decode_scan(data)


def test_sensor_invalid_instance_is_skipped_without_dropping_other_instances(caplog):
    valid = uint(1, 2) + uint(2, 4) + uint(3, 12)
    row = uint(2, 7) + nested(6, valid) + nested(6, valid) + nested(6, uint(1, 255))
    device = decode_scan(nested(2, row), sensors=True).devices[0]
    assert len(device.instances) == 1 and device.instances[0].number == 2
    assert "invalid DALI-2 instance" in caplog.text


@pytest.mark.parametrize(
    "kwargs",
    [
        {"address": -1},
        {"address": 64},
        {"address": True},
        {"address": 7, "mode": 3},
        {"address": 7, "interval_ms": 1},
        {"address": 7, "timeout": float("nan")},
    ],
)
async def test_invalid_identify_arguments_do_not_touch_bus(kwargs):
    programmer = Programmer()
    with pytest.raises(ValueError):
        await SiemensDali2Commissioner(programmer).identify(**kwargs)
    assert not programmer.calls


async def test_long_scan_renews_polling_pause():
    programmer = Programmer()
    commissioner = SiemensDali2Commissioner(programmer, poll_interval=0.001)

    async def override(data, command):
        if command and data[1] == 17:
            commissioner._renew_at = 0
        return None

    programmer.override = override
    await commissioner.discover()
    assert sum(c == ("command", 16, 52, b"\x00\x50\x00") for c in programmer.calls) == 2
    assert programmer.polling == 1


async def test_busy_gateway_times_out_before_sending_scan():
    programmer = Programmer()

    async def override(data, command):
        return bytes((0, 17, 5)) if data[1] == 17 else None

    programmer.override = override
    with pytest.raises(TimeoutError):
        await SiemensDali2Commissioner(programmer, poll_interval=0.001).discover(
            timeout=0.01
        )
    assert programmer.scans == 0 and programmer.polling == 1


async def test_failed_pause_verification_still_restores_polling():
    programmer = Programmer()

    async def override(data, command):
        return b"\x00" if data == b"\x00\x50\x00" else None

    programmer.override = override
    with pytest.raises(DaliCommissioningError, match="polling state"):
        await SiemensDali2Commissioner(programmer).discover()
    assert programmer.scans == 0
    assert ("command", 16, 52, b"\x00\x50\x01") in programmer.calls


@pytest.mark.parametrize("receive_state", [2, 3, 255])
async def test_frame_status_error_stops_move(receive_state):
    programmer = Programmer()

    async def override(data, command):
        if not command and data[1] == 65 and programmer.states.get(65) == 2:
            return bytes((0, 65, 2)) + bytes(6) + bytes((receive_state,))
        return None

    programmer.override = override
    with pytest.raises(DaliCommissioningError, match="not confirmed"):
        await SiemensDali2Commissioner(
            programmer, poll_interval=0.001
        ).change_short_address(7, 12, 0x123456)
    assert programmer.polling == 1 and programmer.ecgs == {7: 0x123456}


async def test_address_change_requires_matching_fresh_readback():
    programmer = Programmer()

    async def override(data, command):
        if command and data[1] == 17 and programmer.scans == 1:
            programmer.ecgs = {7: 0x123456}
        return None

    programmer.override = override
    with pytest.raises(DaliCommissioningError, match="failed verification"):
        await SiemensDali2Commissioner(
            programmer, poll_interval=0.001
        ).change_short_address(7, 12, 0x123456)
    assert programmer.scans == 2 and programmer.polling == 1


async def test_scan_accepts_rc_only_invoke_and_polls_transient_busy():
    programmer = Programmer()
    polls = 0

    async def override(data, command):
        nonlocal polls
        if data[1] != 17:
            return None
        if command:
            programmer.scans += 1
            return b"\x00"
        if programmer.scans:
            polls += 1
            return bytes((0, 17, 5 if polls == 1 else 2))
        return None

    programmer.override = override
    scan = await SiemensDali2Commissioner(programmer, poll_interval=0.001).discover()
    assert len(scan.devices) == 1 and polls == 2


@pytest.mark.parametrize("state", [0, 2, 4])
@pytest.mark.parametrize("with_details", [False, True])
async def test_frame_completion_accepts_short_or_detailed_status(state, with_details):
    programmer = Programmer()

    async def override(data, command):
        if not command and data[1] == 65:
            result = bytes((0, 65, state))
            return result + bytes(6) + b"\x01" if with_details else result
        return None

    programmer.override = override
    scan = await SiemensDali2Commissioner(
        programmer, poll_interval=0.001
    ).change_short_address(7, 12, 0x123456)
    assert scan.devices[0].short_address == 12


async def test_idle_scan_state_does_not_read_a_stale_cache():
    programmer = Programmer()

    async def override(data, command):
        if not command and data[1] == 17:
            return bytes((0, 17, 0))
        return None

    programmer.override = override
    with pytest.raises(DaliCommissioningError):
        await SiemensDali2Commissioner(programmer, poll_interval=0.001).discover()
    assert not any(c[0] == "memory" for c in programmer.calls)
