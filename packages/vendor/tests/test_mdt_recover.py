import pytest
from xknx.exceptions import ManagementConnectionError
from xknx.telegram import IndividualAddress, Telegram
from xknx.telegram.apci import (
    PropertyDescriptionRead,
    PropertyDescriptionResponse,
    PropertyValueRead,
    PropertyValueResponse,
)

from xknxeditor.download import DeviceProgrammer
from xknxeditor.vendor.mdt import encode_symbol, profile_for_application
from xknxeditor.vendor.mdt.symbols import read_symbols, recovered_symbol_entries
from xknxeditor.vendor.mdt.transfer import recover_symbols


class SymbolTransport:
    def __init__(self, symbols, *, failure=None):
        self.symbols = symbols
        self.failure = failure
        self.calls = []
        self.crc_reads = 0

    async def request(self, payload, response_type):
        assert isinstance(payload, PropertyDescriptionRead | PropertyValueRead)
        self.calls.append(payload)
        assert payload.object_index == 5
        if isinstance(payload, PropertyDescriptionRead):
            assert payload.property_id == 150
            response = PropertyDescriptionResponse(
                object_index=5,
                property_id=150,
                type_=17 if self.failure == "width" else 24,
                max_count=len(self.symbols) * 64,
            )
        else:
            if payload.property_id == 151:
                self.crc_reads += 1
                data = self.symbols[payload.start_index - 1][512:][::-1]
                if self.failure == "crc" or (
                    self.failure == "changed" and self.crc_reads > len(self.symbols)
                ):
                    data = bytes([data[0] ^ 1, data[1]])
                if self.failure == "missing_crc":
                    data = b""
            else:
                assert payload.property_id == 150
                if self.failure == "timeout":
                    raise ManagementConnectionError("No pixel read reply")
                pixels = b"".join(s[:512] for s in self.symbols.values())
                offset = (payload.start_index - 1) * 8
                data = pixels[offset : offset + payload.count * 8]
                if self.failure == "short":
                    data = data[:-1]
                if self.failure == "denied":
                    data = b""
            response = PropertyValueResponse(
                object_index=5,
                property_id=payload.property_id,
                count=payload.count if data else 0,
                start_index=payload.start_index,
                data=data,
            )
        return Telegram(
            destination_address=IndividualAddress("1.1.1"), payload=response
        )


@pytest.mark.parametrize(
    "application", ["M-0083_A-018B-31-369F", "M-0083_A-0054-14-0D63"]
)
@pytest.mark.parametrize("apdu", [15, 55, 254])
async def test_recovers_all_pixels_with_real_transport_chunking(application, apdu):
    profile = profile_for_application(application)
    symbols = {
        slot: encode_symbol(bytes([0 if i % (slot + 2) else 255 for i in range(4096)]))
        for slot in range(profile.slots)
    }
    transport = SymbolTransport(symbols)
    programmer = DeviceProgrammer(transport, max_apdu_length=apdu)
    assert await recover_symbols(programmer, profile) == symbols
    frames = [
        p
        for p in transport.calls
        if isinstance(p, PropertyValueRead) and p.property_id == 150
    ]
    offset = 1
    for frame in frames:
        assert frame.start_index == offset
        assert 1 <= frame.count <= 15
        assert (
            len(
                PropertyValueResponse(
                    object_index=5,
                    property_id=150,
                    count=frame.count,
                    start_index=offset,
                    data=bytes(frame.count * 8),
                ).to_knx()
            )
            <= apdu
        )
        offset += frame.count
    assert offset == profile.slots * 64 + 1
    entries = recovered_symbol_entries(symbols, profile)
    assert "Lib" not in entries
    assert {s.slot: s.data for s in read_symbols(entries, profile)} == symbols


@pytest.mark.parametrize(
    "failure,message",
    [
        ("width", "8-byte"),
        ("crc", "differs"),
        ("changed", "changed"),
        ("short", "pixels"),
        ("denied", "pixels"),
        ("missing_crc", "checksum"),
    ],
)
async def test_refuses_unreadable_or_inconsistent_symbol_data(failure, message):
    profile = profile_for_application("M-0083_A-0054-14-0D63")
    symbols = dict.fromkeys(range(profile.slots), encode_symbol(bytes(4096)))
    with pytest.raises(ValueError, match=message):
        await recover_symbols(
            DeviceProgrammer(SymbolTransport(symbols, failure=failure)), profile
        )


async def test_pixel_timeout_remains_a_transport_error_for_recovery_retry():
    profile = profile_for_application("M-0083_A-0054-14-0D63")
    symbols = dict.fromkeys(range(profile.slots), encode_symbol(bytes(4096)))
    with pytest.raises(ManagementConnectionError, match="pixel"):
        await recover_symbols(
            DeviceProgrammer(SymbolTransport(symbols, failure="timeout")), profile
        )


def test_incomplete_library_cannot_be_persisted():
    profile = profile_for_application("M-0083_A-018B-31-369F")
    with pytest.raises(ValueError, match="complete"):
        recovered_symbol_entries({2: encode_symbol(bytes(4096))}, profile)
