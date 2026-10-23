"""Read-only scan tests for the Siemens KNX/DALI Gateway plus N 141/03 flow."""

from __future__ import annotations

from xknxeditor.dali.siemens import (
    MAX_ECG_PER_LINE,
    PROP_COLOUR_TEMP_PHYS_COOLEST,
    PROP_COLOUR_TEMP_PHYS_WARMEST,
    PROP_COLOUR_TYPE_FEATURES,
    PROP_CONNECTED_DEVICE,
    PROP_MIN_PHYS_DIM,
    PROP_RND,
    PROP_SHORT_ADDRESS,
    PROP_TYPES,
    is_siemens_n141_03,
    object_index_for_line,
    scan_n141_03,
)


class _ChunkedFakeProgrammer:
    """FakeProgrammer that respects the requested element ``count`` and ``start_index``.

    Reads are configured as per-pid ``(bytes_payload, element_size)``; a read request slices the
    payload according to the caller's window — mirroring the real A_PropertyValue_Read chunking.
    """

    def __init__(self) -> None:
        self._reads: dict[int, tuple[bytes, int]] = {}
        self.reads: list[dict[str, int]] = []

    def set_table(self, property_id: int, payload: bytes, element_size: int) -> None:
        self._reads[property_id] = (payload, element_size)

    async def read_property(
        self,
        object_index: int,
        property_id: int,
        *,
        count: int = 1,
        start_index: int = 1,
    ) -> bytes:
        self.reads.append(
            {
                "object_index": object_index,
                "property_id": property_id,
                "count": count,
                "start_index": start_index,
            }
        )
        if property_id not in self._reads:
            return b""
        payload, size = self._reads[property_id]
        begin = (start_index - 1) * size
        end = begin + count * size
        return payload[begin : min(end, len(payload))]

    async def write_property(
        self,
        object_index: int,
        property_id: int,
        data: bytes,
        *,
        count: int = 1,
        start_index: int = 1,
    ) -> bytes:
        return b""


def test_object_index_mapping() -> None:
    assert object_index_for_line(0) == 35  # line 1
    assert object_index_for_line(1) == 36  # Twin line 2


def test_is_siemens_n141_03_matches_known_app_id() -> None:
    assert is_siemens_n141_03("M-0001_A-9837-11-449E")  # device sees in the project
    assert not is_siemens_n141_03("M-0001_A-0248")  # N141/14-32 modern DALI-2 DCA
    assert not is_siemens_n141_03("M-0083_A-0154-40-0F69-O00EF")  # MDT, not Siemens
    assert not is_siemens_n141_03(None)


async def test_scan_decodes_arrays_and_marks_present_ecgs() -> None:
    prog = _ChunkedFakeProgrammer()

    # Three present ECGs at slots 0, 3, 7; rest are 0xFF (unassigned).
    short = bytearray([0xFF] * MAX_ECG_PER_LINE)
    short[0] = 0
    short[3] = 3
    short[7] = 7
    prog.set_table(PROP_SHORT_ADDRESS, bytes(short), 1)

    rnd = bytearray(3 * MAX_ECG_PER_LINE)
    rnd[0:3] = bytes((0x12, 0x34, 0x56))  # slot 0 random = 0x123456
    rnd[9:12] = bytes((0xAB, 0xCD, 0xEF))  # slot 3
    prog.set_table(PROP_RND, bytes(rnd), 3)

    types = bytearray(8 * MAX_ECG_PER_LINE)
    types[0] = 0  # ECG
    types[24] = 6  # slot 3 -> LED module
    types[56] = 8  # slot 7 -> colour control
    prog.set_table(PROP_TYPES, bytes(types), 8)

    min_phys = bytearray([0x80] * MAX_ECG_PER_LINE)
    prog.set_table(PROP_MIN_PHYS_DIM, bytes(min_phys), 1)

    connected = bytearray([1] * MAX_ECG_PER_LINE)
    prog.set_table(PROP_CONNECTED_DEVICE, bytes(connected), 1)

    cool = bytearray(2 * MAX_ECG_PER_LINE)
    cool[14:16] = bytes((0x0F, 0xA0))  # slot 7 -> 4000 K (0x0FA0)
    prog.set_table(PROP_COLOUR_TEMP_PHYS_COOLEST, bytes(cool), 2)
    warm = bytearray(2 * MAX_ECG_PER_LINE)
    warm[14:16] = bytes((0x27, 0x10))  # slot 7 -> 10000 K (big-endian in frame)
    prog.set_table(PROP_COLOUR_TEMP_PHYS_WARMEST, bytes(warm), 2)
    prog.set_table(PROP_COLOUR_TYPE_FEATURES, bytes([0xC0] * MAX_ECG_PER_LINE), 1)

    state = await scan_n141_03(prog, line=0)

    assert state.line == 0 and state.object_index == 35
    assert len(state.ecgs) == MAX_ECG_PER_LINE
    present = {e.slot: e for e in state.present_ecgs}
    assert set(present) == {0, 3, 7}

    assert present[0].short_address == 0
    assert present[0].random_address == 0x123456
    assert present[0].type == 0  # ECG
    assert present[0].min_phys_dim_value == 0x80

    assert present[3].short_address == 3
    assert present[3].random_address == 0xABCDEF
    assert present[3].type == 6  # LED_MODULE
    assert present[3].connected_device == 1

    assert present[7].type == 8  # COLOUR_CONTROL
    assert present[7].colour_temp_phys_coolest == 0x0FA0  # 4000 K
    assert present[7].colour_temp_phys_warmest == 0x2710  # 10000 K
    assert present[7].colour_type_features == 0xC0


async def test_scan_only_reads_optional_arrays_when_asked() -> None:
    prog = _ChunkedFakeProgrammer()
    prog.set_table(PROP_SHORT_ADDRESS, bytes([0xFF] * MAX_ECG_PER_LINE), 1)
    prog.set_table(PROP_RND, bytes(3 * MAX_ECG_PER_LINE), 3)
    prog.set_table(PROP_TYPES, bytes(8 * MAX_ECG_PER_LINE), 8)
    prog.set_table(PROP_MIN_PHYS_DIM, bytes(MAX_ECG_PER_LINE), 1)
    prog.set_table(PROP_CONNECTED_DEVICE, bytes(MAX_ECG_PER_LINE), 1)
    prog.set_table(PROP_COLOUR_TEMP_PHYS_COOLEST, bytes(2 * MAX_ECG_PER_LINE), 2)
    prog.set_table(PROP_COLOUR_TEMP_PHYS_WARMEST, bytes(2 * MAX_ECG_PER_LINE), 2)
    prog.set_table(PROP_COLOUR_TYPE_FEATURES, bytes(MAX_ECG_PER_LINE), 1)

    await scan_n141_03(prog, line=0, with_cin=False, with_emergency=False)
    pids_read = {r["property_id"] for r in prog.reads}
    assert 58 not in pids_read  # PROP_CIN gated by with_cin
    assert 71 not in pids_read and 72 not in pids_read  # emergency levels
    assert 73 not in pids_read  # DevType1 features
    assert 74 not in pids_read  # failure flags (also fw-gated)


async def test_scan_requests_use_object_index_and_chunks_to_15() -> None:
    prog = _ChunkedFakeProgrammer()
    prog.set_table(PROP_SHORT_ADDRESS, bytes([0xFF] * MAX_ECG_PER_LINE), 1)
    prog.set_table(PROP_RND, bytes(3 * MAX_ECG_PER_LINE), 3)
    prog.set_table(PROP_TYPES, bytes(8 * MAX_ECG_PER_LINE), 8)
    prog.set_table(PROP_MIN_PHYS_DIM, bytes(MAX_ECG_PER_LINE), 1)
    prog.set_table(PROP_CONNECTED_DEVICE, bytes(MAX_ECG_PER_LINE), 1)
    prog.set_table(PROP_COLOUR_TEMP_PHYS_COOLEST, bytes(2 * MAX_ECG_PER_LINE), 2)
    prog.set_table(PROP_COLOUR_TEMP_PHYS_WARMEST, bytes(2 * MAX_ECG_PER_LINE), 2)
    prog.set_table(PROP_COLOUR_TYPE_FEATURES, bytes(MAX_ECG_PER_LINE), 1)

    await scan_n141_03(prog, line=1)

    assert all(r["object_index"] == 36 for r in prog.reads)  # line 1 -> odx 36
    # 64 elements split into 15 + 15 + 15 + 15 + 4 means every property triggers 5 reads.
    short_requests = [r for r in prog.reads if r["property_id"] == PROP_SHORT_ADDRESS]
    assert [r["count"] for r in short_requests] == [15, 15, 15, 15, 4]
    assert [r["start_index"] for r in short_requests] == [1, 16, 31, 46, 61]
