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


class GearProgrammer(Programmer):
    def __init__(self):
        super().__init__()
        self.status = 2
        self.level = 99
        self.last_frame = None
        self.receive_state = 0
        self.delayed = 0

    async def function_property_raw(self, object_index, property_id, data, *, command):
        result = await super().function_property_raw(
            object_index, property_id, data, command=command
        )
        if data[1] != 65:
            return result
        if command:
            self.last_frame = data[6:8]
            if not self.last_frame[0] & 1:
                self.level = min(self.last_frame[1], 200)
            return result
        if self.last_frame is None:
            return result
        value = self.status if self.last_frame[1] == 144 else self.level
        receive = self.receive_state
        if self.delayed:
            self.delayed -= 1
            receive = 1
        return bytes((0, 65, 2, 0, 0, 0, value, 0, 8, receive))


class ColorProgrammer(Programmer):
    def __init__(self):
        super().__init__()
        self.mirek = 370
        self.channels = bytes((12, 34, 56, 78))
        self.busy = {}
        self.color_response = None

    async def function_property_raw(self, object_index, property_id, data, *, command):
        result = await super().function_property_raw(
            object_index, property_id, data, command=command
        )
        if data[1] != 96:
            return result
        sub = data[2]
        if self.color_response is not None:
            result = self.color_response(data, command)
            if result is not None:
                return result
        if self.busy.get(sub, 0):
            self.busy[sub] -= 1
            return b"\xfe"
        line = object_index - 16
        if sub in (1, 5):
            assert not command and len(data) == 4
            return bytes((0, 96, sub, data[3], line))
        if sub in (3, 7):
            assert not command and data[4] == line
            return bytes((0, 96, sub, (line << 6) | data[3])) + (
                self.mirek.to_bytes(2, "big") if sub == 7 else self.channels
            )
        assert command and sub in (2, 6)
        return b"\x00"


@pytest.mark.parametrize("line", [0, 1])
@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("temperature", [False, True])
async def test_color_protocol_uses_line_address_and_returns_observed_value(
    line, write, temperature
):
    programmer = ColorProgrammer()
    commissioner = SiemensDali2Commissioner(programmer, line)
    if temperature:
        result = await commissioner.color_temperature(63, 4000 if write else None)
        assert result.mirek == 370 and result.kelvin == 2702
        expected = [
            ("state", bytes((0, 96, 5, 63))),
            ("state", bytes((0, 96, 7, 63, line))),
        ]
        if write:
            expected.insert(0, ("command", bytes((0, 96, 6, 63, 0, 250))))
    else:
        result = await commissioner.rgbw(63, (100, 200, 0, 255) if write else None)
        assert (result.red, result.green, result.blue, result.white) == (12, 34, 56, 78)
        expected = [
            ("state", bytes((0, 96, 1, 63))),
            ("state", bytes((0, 96, 3, 63, line))),
        ]
        if write:
            expected.insert(0, ("command", bytes((0, 96, 2, 63, 100, 200, 0, 255))))
    assert (result.line, result.address) == (line, 63)
    assert [
        c for c in programmer.calls if c[0] in ("state", "command") and c[-1][1] == 96
    ] == [(kind, 16 + line, 52, data) for kind, data in expected]
    assert programmer.polling == 1


@pytest.mark.parametrize("mirek", [0, 65535])
async def test_color_temperature_unavailable_does_not_invent_kelvin(mirek):
    programmer = ColorProgrammer()
    programmer.mirek = mirek
    result = await SiemensDali2Commissioner(programmer).color_temperature(7)
    assert result.mirek == mirek and result.kelvin is None


@pytest.mark.parametrize("kelvin,mirek", [(1000, 1000), (20000, 50), (2700, 370)])
async def test_temperature_boundaries_and_rounding_encode_once(kelvin, mirek):
    programmer = ColorProgrammer()
    await SiemensDali2Commissioner(programmer).color_temperature(0, kelvin)
    writes = [c[-1] for c in programmer.calls if c[0] == "command" and c[-1][1] == 96]
    assert writes == [bytes((0, 96, 6, 0)) + mirek.to_bytes(2, "big")]


async def test_color_long_wait_renews_polling_pause_and_keeps_initial_paused_state(
    monkeypatch,
):
    programmer = ColorProgrammer()
    programmer.polling = 0
    programmer.busy = {3: 8}
    loop = asyncio.get_running_loop()
    clock = [loop.time()]
    monkeypatch.setattr(loop, "time", lambda: clock[0])

    async def advance(seconds):
        clock[0] += seconds

    monkeypatch.setattr(asyncio, "sleep", advance)
    result = await SiemensDali2Commissioner(programmer).rgbw(7)
    assert result.white == 78 and programmer.polling == 0
    polling = [c[-1] for c in programmer.calls if c[0] == "command" and c[-1][1] == 80]
    assert polling == [bytes((0, 80, 0))] * 2


@pytest.mark.parametrize("temperature", [False, True])
async def test_color_busy_retries_same_request_without_mutating_values(
    monkeypatch, temperature
):
    programmer = ColorProgrammer()
    programmer.busy = {1: 1, 2: 1, 3: 2, 5: 1, 6: 1, 7: 2}
    delays = []

    async def wait(delay):
        delays.append(delay)

    monkeypatch.setattr(asyncio, "sleep", wait)
    commissioner = SiemensDali2Commissioner(programmer)
    if temperature:
        await commissioner.color_temperature(7, 4000)
        request = bytes((0, 96, 6, 7, 0, 250))
    else:
        await commissioner.rgbw(7, (12, 34, 56, 255))
        request = bytes((0, 96, 2, 7, 12, 34, 56, 255))
    writes = [c[-1] for c in programmer.calls if c[0] == "command" and c[-1][1] == 96]
    assert writes == [request, request]
    assert delays == [0.25 if temperature else 0.75] * 4
    assert programmer.polling == 1


async def test_color_busy_response_body_is_not_a_completed_result(monkeypatch):
    programmer = ColorProgrammer()
    responses = [b"\xfe\x00\x99", None]
    programmer.color_response = lambda data, command: (
        responses.pop(0) if data[2] == 7 else None
    )

    async def wait(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", wait)
    result = await SiemensDali2Commissioner(programmer).color_temperature(7)
    assert result.mirek == 370 and not responses and programmer.polling == 1


@pytest.mark.parametrize(
    "sub,response",
    [
        (5, b""),
        (5, b"\x00"),
        (5, bytes((0, 95, 5, 7, 0))),
        (5, bytes((0, 96, 5, 8, 0))),
        (5, bytes((0, 96, 5, 7, 1))),
        (7, bytes((0, 96, 7, 7, 1))),
        (7, bytes((0, 96, 7, 71, 1, 114))),
        (7, bytes((0, 96, 7, 8, 1, 114))),
        (7, b"\xff"),
        (1, bytes((0, 96, 1, 7))),
        (3, bytes((0, 96, 3, 7, 1, 2, 3))),
        (3, bytes((0, 96, 3, 71, 1, 2, 3, 4))),
        (2, b"\xff"),
        (6, b"\xff"),
    ],
)
async def test_color_rejects_malformed_failed_or_wrong_target_response(sub, response):
    programmer = ColorProgrammer()
    programmer.color_response = lambda data, command: (
        response if data[2] == sub else None
    )
    commissioner = SiemensDali2Commissioner(programmer)
    with pytest.raises(DaliCommissioningError):
        if sub >= 5:
            await commissioner.color_temperature(7, 4000 if sub == 6 else None)
        else:
            await commissioner.rgbw(7, (1, 2, 3, 4) if sub == 2 else None)
    assert programmer.polling == 1


@pytest.mark.parametrize("temperature", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_color_busy_timeout_and_cancellation_restore_polling(temperature, cancel):
    programmer = ColorProgrammer()
    programmer.busy = {7 if temperature else 3: 10000}
    commissioner = SiemensDali2Commissioner(programmer)
    operation = commissioner.color_temperature if temperature else commissioner.rgbw
    task = asyncio.create_task(operation(7, timeout=15 if cancel else 0.01))
    if cancel:
        while not any(c[0] == "state" and c[-1][1] == 96 for c in programmer.calls):
            await asyncio.sleep(0)
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert programmer.polling == 1


@pytest.mark.parametrize(
    "method,value",
    [
        ("color_temperature", 0),
        ("color_temperature", 20001),
        ("color_temperature", True),
        ("rgbw", (1, 2, 3)),
        ("rgbw", (1, 2, 3, 256)),
        ("rgbw", (1, 2, 3, True)),
    ],
)
async def test_invalid_colors_fail_without_bus_io(method, value):
    programmer = ColorProgrammer()
    with pytest.raises(ValueError):
        await getattr(SiemensDali2Commissioner(programmer), method)(7, value)
    assert not programmer.calls


@pytest.mark.parametrize("line", [0, 1])
async def test_query_gear_reads_status_and_level_and_restores_polling(line):
    programmer = GearProgrammer()
    programmer.delayed = 2
    result = await SiemensDali2Commissioner(
        programmer, line, poll_interval=0.001
    ).read_gear_state(7)
    assert (result.line, result.address, result.status, result.level) == (
        line,
        7,
        2,
        99,
    )
    assert result.status_receive_state == result.level_receive_state == 0
    assert programmer.polling == 1
    commands = [call for call in programmer.calls if call[0] == "command"]
    assert [call[-1] for call in commands] == [
        bytes((0, 80, 0)),
        bytes((0, 65, 3, 3, 0, 0, 15, 144, 16, 1)),
        bytes((0, 65, 3, 3, 0, 0, 15, 160, 16, 1)),
        bytes((0, 80, 1)),
    ]
    assert all(call[1:3] == (16 + line, 52) for call in commands)
    assert not any(call[0] == "memory" for call in programmer.calls)


@pytest.mark.parametrize("level", [0, 127, 254])
async def test_direct_level_returns_actual_result_not_requested_level(level):
    programmer = GearProgrammer()
    result = await SiemensDali2Commissioner(programmer).set_level(7, level)
    assert result.level == min(level, 200)
    frames = [
        call[-1][6:8]
        for call in programmer.calls
        if call[0] == "command" and call[-1][1] == 65
    ]
    assert frames == [bytes((14, level)), bytes((15, 144)), bytes((15, 160))]
    assert programmer.polling == 1


@pytest.mark.parametrize("receive", [1, 2])
async def test_missing_or_undecodable_query_never_becomes_zero_or_healthy(receive):
    programmer = GearProgrammer()
    programmer.receive_state = receive
    result = await SiemensDali2Commissioner(
        programmer, poll_interval=0.001
    ).read_gear_state(7)
    assert result.status is result.level is None
    assert result.status_receive_state == result.level_receive_state == receive
    assert programmer.polling == 1


async def test_mask_level_is_not_reported_as_brightness():
    programmer = GearProgrammer()
    programmer.level = 255
    result = await SiemensDali2Commissioner(programmer).read_gear_state(7)
    assert result.level is None and result.status == 2


@pytest.mark.parametrize("ongoing", [1, 4, 5])
async def test_query_waits_through_transient_busy_after_send_acknowledgement(ongoing):
    programmer = Programmer()
    sent = False
    responses = []

    async def override(data, command):
        nonlocal sent
        if data[1] == 65:
            if command:
                sent = True
                responses.extend(
                    [
                        bytes((0, 65, 2)) + bytes(6) + bytes((1,)),
                        bytes((0, 65, ongoing)),
                        bytes((0, 65, 2, 0, 0, 0, 12, 0, 8, 0)),
                    ]
                )
            elif sent and responses:
                return responses.pop(0)
        return None

    programmer.override = override
    result = await SiemensDali2Commissioner(
        programmer, poll_interval=0.001
    ).read_gear_state(7)
    assert result.status == result.level == 12 and programmer.polling == 1


@pytest.mark.parametrize("level", [-1, 255, True, 2.5])
async def test_invalid_level_is_rejected_without_any_transport_call(level):
    programmer = GearProgrammer()
    with pytest.raises(ValueError, match="arc level"):
        await SiemensDali2Commissioner(programmer).set_level(7, level)
    assert not programmer.calls


@pytest.mark.parametrize(
    "response",
    [bytes((0, 65, 2)), bytes((0, 65, 2)) + bytes(6) + bytes((3,)), bytes((0, 65, 3))],
)
async def test_invalid_query_response_raises_and_restores_polling(response):
    programmer = Programmer()
    sent = False

    async def override(data, command):
        nonlocal sent
        if data[1] == 65:
            if command:
                sent = True
            elif sent:
                return response
        return None

    programmer.override = override
    with pytest.raises(DaliCommissioningError):
        await SiemensDali2Commissioner(programmer).read_gear_state(7)
    assert programmer.polling == 1


@pytest.mark.parametrize("cancel", [False, True])
async def test_pending_query_timeout_or_cancel_restores_polling(cancel):
    programmer = Programmer()
    started = asyncio.Event()

    async def override(data, command):
        if data[1] == 65:
            if command:
                started.set()
            elif started.is_set():
                return bytes((0, 65, 4))
        return None

    programmer.override = override
    commissioner = SiemensDali2Commissioner(programmer, poll_interval=0.001)
    task = asyncio.create_task(
        commissioner.read_gear_state(7, timeout=1 if cancel else 0.02)
    )
    await started.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
        await task
    assert programmer.polling == 1


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


class LegacyProgrammer(Programmer):
    def __init__(self, identity=b"\x05\x8b\x12\x00"):
        super().__init__()
        self.identity = identity

    async def read_property(self, object_index, property_id, **kwargs):
        if (object_index, property_id) == (4, 246):
            self.calls.append(("identity", kwargs))
            return self.identity
        return await super().read_property(object_index, property_id, **kwargs)


def legacy_frames(programmer):
    return [
        call[-1]
        for call in programmer.calls
        if call[0] == "command" and call[-1][1] == 65
    ]


@pytest.mark.parametrize("sensors", [False, True])
async def test_legacy_identification_checks_hardware_then_stops_and_restores_polling(
    sensors,
):
    programmer = LegacyProgrammer()
    await SiemensDali2Commissioner(programmer, 1).identify_legacy(
        7, lines=2, sensors=sensors, duration=0.001
    )
    frames = legacy_frames(programmer)
    assert frames == [
        bytes.fromhex("00410703000ffe001801" if sensors else "0041030300000f051001"),
        bytes.fromhex("0041030300c100001801" if sensors else "0041030300000f001001"),
    ]
    assert programmer.calls[0] == ("identity", {"count": 4})
    assert programmer.polling == 1
    assert not any(
        call[0] == "command" and call[-1][1] == 96 for call in programmer.calls
    )


@pytest.mark.parametrize(
    "identity", [b"", bytes.fromhex("058b1100"), bytes.fromhex("058b1201"), b"xxxx"]
)
async def test_legacy_wrong_hardware_never_writes(identity):
    programmer = LegacyProgrammer(identity)
    with pytest.raises(DaliCommissioningError, match="hardware"):
        await SiemensDali2Commissioner(programmer).identify_legacy(7, lines=2)
    assert not any(call[0] == "command" for call in programmer.calls)


@pytest.mark.parametrize("sensors", [False, True])
async def test_legacy_cancellation_still_stops_identification(sensors):
    programmer = LegacyProgrammer()
    task = asyncio.create_task(
        SiemensDali2Commissioner(programmer).identify_legacy(
            7, lines=2, sensors=sensors
        )
    )
    while not legacy_frames(programmer):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert legacy_frames(programmer)[-1] == bytes.fromhex(
        "0041030300c100001801" if sensors else "0041030300000f001001"
    )
    assert programmer.polling == 1


@pytest.mark.parametrize("mode,command", [(1, 5), (2, 0)])
async def test_legacy_gear_on_off_uses_targeted_command(mode, command):
    programmer = LegacyProgrammer(bytes.fromhex("058b1100"))
    await SiemensDali2Commissioner(programmer).identify_legacy(63, lines=1, mode=mode)
    assert legacy_frames(programmer) == [
        bytes((0, 65, 3, 3, 0, 0, 127, command, 16, 1))
    ]


async def test_legacy_stop_before_start_performs_no_bus_access():
    programmer = LegacyProgrammer()
    await SiemensDali2Commissioner(programmer).identify_legacy(
        7, lines=2, stopped=lambda: True
    )
    assert not programmer.calls


async def test_legacy_failure_attempts_stop_and_keeps_error():
    programmer = LegacyProgrammer()

    async def reject(data, command):
        if command and data[1] == 65 and data[7] == 5:
            return b"\xff"
        return None

    programmer.override = reject
    with pytest.raises(DaliCommissioningError):
        await SiemensDali2Commissioner(programmer).identify_legacy(7, lines=2)
    assert legacy_frames(programmer)[-1] == bytes.fromhex("0041030300000f001001")
    assert programmer.polling == 1


async def test_legacy_timer_uses_maximum_minimum_and_sensor_refresh(monkeypatch):
    import xknxeditor.dali.siemens_dali2 as module

    loop = asyncio.get_running_loop()
    clock = [loop.time()]
    monkeypatch.setattr(loop, "time", lambda: clock[0])

    async def advance(seconds):
        clock[0] += seconds

    monkeypatch.setattr(module.asyncio, "sleep", advance)
    programmer = LegacyProgrammer()
    await SiemensDali2Commissioner(programmer).identify_legacy(7, lines=2, duration=4.6)
    assert [frame[7] for frame in legacy_frames(programmer)] == [5, 6, 5, 0]
    programmer = LegacyProgrammer()
    await SiemensDali2Commissioner(programmer).identify_legacy(
        7, lines=2, sensors=True, duration=4.1
    )
    assert [frame[5:8] for frame in legacy_frames(programmer)] == [
        b"\x0f\xfe\0",
        b"\x0f\xfe\0",
        b"\xc1\0\0",
    ]
