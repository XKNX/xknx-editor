import pytest

from xknxeditor.vendor.mdt import encode_symbol, profile_for_application
from xknxeditor.vendor.mdt.transfer import compare_symbols, program_symbols


class Connection:
    def __init__(self, data: bytes, status: bytes = b"\x02") -> None:
        self.data = data
        self.status = status
        self.calls: list[tuple] = []

    async def write_property(
        self, obj, prop, data, *, count=1, start_index=1, verify=True
    ):
        self.calls.append(("write", obj, prop, data, count, start_index))
        return data

    async def read_property(self, obj, prop, *, count=1, start_index=1):
        self.calls.append(("read", obj, prop, count, start_index))
        return self.status if prop == 152 else self.data[512:][::-1]

    async def restart(self):
        self.calls.append(("restart",))


async def no_sleep(_seconds):
    pass


async def test_compare_reads_only_and_transfer_checks_device() -> None:
    profile = profile_for_application("M-0083_A-0054-14-0D63")
    assert profile is not None
    data = encode_symbol(bytes([127] * 4096))
    connection = Connection(data)
    assert await compare_symbols(connection, profile, {19: data}) == {19: True}
    assert connection.calls == [("read", 5, 151, 1, 20)]
    connection.calls.clear()
    assert await program_symbols(connection, profile, {19: data}, sleep=no_sleep) == 1
    assert connection.calls == [
        ("write", 5, 153, b"\x01", 1, 1),
        ("write", 5, 150, data[:512], 64, 1217),
        ("write", 5, 153, b"\x02", 1, 1),
        ("read", 5, 152, 1, 1),
        ("write", 5, 153, b"\x00", 1, 1),
        ("read", 5, 151, 1, 20),
        ("restart",),
    ]


async def test_invalid_data_never_reaches_device_and_failed_transfer_resets() -> None:
    profile = profile_for_application("M-0083_A-0054-14-0D63")
    assert profile is not None
    data = encode_symbol(bytes(4096))
    connection = Connection(data, b"\x01")
    with pytest.raises(ValueError, match="slot"):
        await program_symbols(connection, profile, {20: data}, sleep=no_sleep)
    assert not connection.calls
    with pytest.raises(ValueError, match="rejected"):
        await program_symbols(connection, profile, {0: data}, sleep=no_sleep)
    assert connection.calls[-2:] == [("write", 5, 153, b"\x00", 1, 1), ("restart",)]


async def test_cleanup_failure_keeps_original_error() -> None:
    class Failing(Connection):
        async def write_property(self, obj, prop, data, **kwargs):
            await super().write_property(obj, prop, data, **kwargs)
            if prop == 150:
                raise OSError("transfer failed")
            if prop == 153 and data == b"\x00":
                raise OSError("reset failed")
            return data

        async def restart(self):
            await super().restart()
            raise OSError("restart failed")

    profile = profile_for_application("M-0083_A-0054-14-0D63")
    data = encode_symbol(bytes(4096))
    connection = Failing(data)
    with pytest.raises(OSError, match="transfer failed") as error:
        await program_symbols(connection, profile, {0: data}, sleep=no_sleep)
    assert len(error.value.__notes__) == 2
    assert connection.calls[-1] == ("restart",)


async def test_cancel_transfer_resets_before_restart() -> None:
    import asyncio

    profile = profile_for_application("M-0083_A-0054-14-0D63")
    data = encode_symbol(bytes(4096))
    connection = Connection(data)

    async def cancel(_seconds):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await program_symbols(connection, profile, {0: data}, sleep=cancel)
    assert connection.calls[-2:] == [("write", 5, 153, b"\x00", 1, 1), ("restart",)]


async def test_property_transport_chunks_complete_symbol_without_gaps() -> None:
    from xknx.telegram import IndividualAddress, Telegram
    from xknx.telegram.apci import (
        PropertyValueRead,
        PropertyValueResponse,
        PropertyValueWrite,
        Restart,
    )

    from xknxeditor.download import DeviceProgrammer

    data = encode_symbol(bytes(range(256)) * 16)
    profile = profile_for_application("M-0083_A-018B-31-369F")
    calls = []

    class Transport:
        async def request(self, payload, expected):
            calls.append(payload)
            if isinstance(payload, PropertyValueWrite):
                response = payload.data
            else:
                assert isinstance(payload, PropertyValueRead)
                response = b"\x02" if payload.property_id == 152 else data[512:][::-1]
            return Telegram(
                destination_address=IndividualAddress("1.1.1"),
                payload=PropertyValueResponse(
                    object_index=payload.object_index,
                    property_id=payload.property_id,
                    start_index=payload.start_index,
                    count=payload.count,
                    data=response,
                ),
            )

        async def send_data(self, payload, wait_for_ack=True):
            calls.append(payload)

    programmer = DeviceProgrammer(Transport(), max_apdu_length=55)
    assert await program_symbols(programmer, profile, {35: data}, sleep=no_sleep) == 1
    frames = [
        p for p in calls if isinstance(p, PropertyValueWrite) and p.property_id == 150
    ]
    assert b"".join(p.data for p in frames) == data[:512]
    offset = 35 * 64 + 1
    for frame in frames:
        assert frame.start_index == offset
        assert len(frame.data) == frame.count * 8
        assert 1 <= frame.count <= 15 and len(frame.to_knx()) <= 55
        offset += frame.count
    assert offset == 36 * 64 + 1
    assert isinstance(calls[-1], Restart)
    calls.clear()
    with pytest.raises(ValueError, match="slot"):
        await program_symbols(programmer, profile, {0: data}, sleep=no_sleep)
    assert not calls


@pytest.mark.parametrize(
    "application,slots,refresh",
    [
        ("M-0083_A-018B-31-369F", 36, True),
        ("M-0083_A-0054-14-0D63", 20, False),
    ],
)
async def test_complete_comparison_and_synchronization(application, slots, refresh):
    from xknxeditor.vendor.mdt.transfer import synchronize_symbols

    profile = profile_for_application(application)
    data = encode_symbol(bytes(range(256)) * 16)
    symbols = dict.fromkeys(range(slots), data)
    connection = Connection(data)
    assert all((await compare_symbols(connection, profile, symbols)).values())
    assert connection.calls == [("read", 5, 151, 1, i + 1) for i in range(slots)]
    connection.calls.clear()
    assert (
        len(await synchronize_symbols(connection, profile, symbols, sleep=no_sleep))
        == slots
    )
    controls = [("write", 5, 153, bytes([v]), 1, 1) for v in (2, 0)] if refresh else []
    assert connection.calls == controls + [
        ("read", 5, 151, 1, i + 1) for i in range(slots)
    ]
    connection.calls.clear()
    with pytest.raises(ValueError, match="complete"):
        await synchronize_symbols(connection, profile, {2: data}, sleep=no_sleep)
    assert not connection.calls


async def test_cancel_synchronization_resets_without_restart():
    import asyncio

    from xknxeditor.vendor.mdt.transfer import synchronize_symbols

    profile = profile_for_application("M-0083_A-018B-31-369F")
    data = encode_symbol(bytes(4096))
    connection = Connection(data)

    async def cancel(_seconds):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await synchronize_symbols(
            connection, profile, dict.fromkeys(range(36), data), sleep=cancel
        )
    assert connection.calls == [("write", 5, 153, bytes([v]), 1, 1) for v in (2, 0)]


async def test_full_original_defaults_write_protected_slots_only_with_verified_pixels():
    from pathlib import Path

    from xknxeditor.vendor.mdt.symbols import read_symbol_library

    path = (
        Path(__file__).resolve().parents[3]
        / ".references/mdtdca/MDT_DCA_BE_01_Push_Button_II_Smart_v4_5_2_0.etsapp"
    )
    if not path.exists():
        pytest.skip("Optional original library is not installed")
    profile = profile_for_application("M-0083_A-018B-31-369F")

    class LibraryConnection(Connection):
        async def read_property(self, obj, prop, **kwargs):
            await super().read_property(obj, prop, **kwargs)
            return (
                b"\x02"
                if prop == 152
                else payload[kwargs["start_index"] - 1][512:][::-1]
            )

    for new in (False, True):
        for second in (False, True):
            payload = {
                s.slot: s.data
                for s in read_symbol_library(
                    path.read_bytes(),
                    profile,
                    new_library=new,
                    second_generation=second,
                )
            }
            connection = LibraryConnection(payload[0])
            assert (
                await program_symbols(connection, profile, payload, sleep=no_sleep)
                == 36
            )
            pixels = [c for c in connection.calls if c[:3] == ("write", 5, 150)]
            assert [c[5] for c in pixels] == [slot * 64 + 1 for slot in range(36)]
            assert [c[3] for c in pixels] == [payload[slot][:512] for slot in range(36)]
            controls = [c[3] for c in connection.calls if c[:3] == ("write", 5, 153)]
            assert controls == [b"\x01", b"\x02", b"\x00"]
            connection.calls.clear()
            payload[1] = payload[2]
            with pytest.raises(ValueError, match="Protected"):
                await program_symbols(connection, profile, payload, sleep=no_sleep)
            assert not connection.calls


async def test_complete_central_library_batches_pixels_before_checking_crcs():
    profile = profile_for_application("M-0083_A-0054-14-0D63")
    data = encode_symbol(bytes(4096))
    connection = Connection(data)
    assert (
        await program_symbols(
            connection, profile, dict.fromkeys(range(20), data), sleep=no_sleep
        )
        == 20
    )
    assert connection.calls == [
        ("write", 5, 153, b"\x01", 1, 1),
        *[("write", 5, 150, data[:512], 64, i * 64 + 1) for i in range(20)],
        ("write", 5, 153, b"\x02", 1, 1),
        ("read", 5, 152, 1, 1),
        *[("read", 5, 151, 1, i + 1) for i in range(20)],
        ("write", 5, 153, b"\x00", 1, 1),
        ("restart",),
    ]
