"""Tests for the LVS 2 ".prj" reader/writer.

The synthetic tests check the module's internal contract (lossless round-trip, mapped-field edits,
malformed input). ``test_real_lvs_prj_*`` validate against a GENUINE ``.prj`` saved by the original
LVS 2 program (``fixtures/sample_triton.prj``): the decoded content matches what LVS shows and the
file round-trips byte-exact, so the framing and the mapped fields are confirmed against real output
(not just internally consistent).
"""

from __future__ import annotations

import pytest

from xknxeditor.vendor.triton.model import (
    DeviceType,
    LineDataType,
)
from xknxeditor.vendor.triton.prj import (
    HEADER_SIZE,
    LINE_SIZE,
    LN_FLAGS,
    LN_TEXT_OFF,
    LN_TYPE,
    MAGIC,
    REC_FAMILY,
    REC_LINES,
    REC_NAME,
    RECORD_SIZE,
    TYPE_EIS6,
    TYPE_FREE,
    PrjError,
    read_prj,
    write_prj,
)


def _non_canonical(length: int, seed: int) -> bytes:
    """A block of distinctive non-zero bytes, to prove unmapped bytes survive verbatim."""
    return bytes((seed + i) % 251 + 1 for i in range(length))


def _shortstring(text: str, capacity: int, filler_seed: int) -> bytes:
    """A ShortString[capacity]: length byte, the text, then NON-zero junk in the unused tail."""
    raw = text.encode("latin-1")[:capacity]
    tail = _non_canonical(capacity - len(raw), filler_seed)
    return bytes([len(raw)]) + raw + tail


def _line_record(
    *,
    type_byte: int,
    text_off: str,
    text_on: str,
    flags: int,
    numfmt_low_nibble: int,
    seed: int,
) -> bytes:
    """Build one 113-byte line record with the mapped fields set and junk everywhere else."""
    buf = bytearray(_non_canonical(LINE_SIZE, seed))
    buf[LN_TEXT_OFF : LN_TEXT_OFF + 41] = _shortstring(text_off, 40, seed + 1)
    buf[0x29 : 0x29 + 41] = _shortstring(text_on, 40, seed + 2)
    buf[LN_TYPE] = type_byte
    # numeric-format byte: the high nibble is mapped, the low nibble must be preserved verbatim.
    buf[0x53] = 0x10 | (numfmt_low_nibble & 0x0F)
    buf[0x54] = 3  # first digit position
    buf[0x55] = 2  # integer digits
    buf[0x56] = 1  # decimal digits
    buf[LN_FLAGS] = flags
    assert len(buf) == LINE_SIZE
    return bytes(buf)


def _display_record(
    *, name: str, family: int, subtype: int, address: int, seed: int
) -> bytes:
    """Build one 965-byte display record to the documented layout."""
    buf = bytearray(_non_canonical(RECORD_SIZE, seed))
    buf[REC_NAME : REC_NAME + 41] = _shortstring(name, 40, seed + 3)
    buf[0x32] = address & 0xFF
    buf[0x33] = (address >> 8) & 0xFF
    buf[REC_FAMILY] = family
    buf[0x35] = subtype
    lines = [
        _line_record(
            type_byte=TYPE_EIS6,
            text_off="Temperatur",
            text_on="",
            flags=(5 << 3) | 0x01,  # object 5, receive
            numfmt_low_nibble=0x0A,
            seed=seed + 10,
        ),
        _line_record(
            type_byte=0x00,  # EIS1 switch
            text_off="AUS",
            text_on="EIN",
            flags=(2 << 3) | 0x07,  # object 2, receive+send+alarm
            numfmt_low_nibble=0x00,
            seed=seed + 20,
        ),
    ]
    # the remaining slots are free, with junk in the unmapped bytes
    for i in range(2, 8):
        lines.append(
            _line_record(
                type_byte=TYPE_FREE,
                text_off="",
                text_on="",
                flags=0x00,
                numfmt_low_nibble=0x00,
                seed=seed + 30 + i,
            )
        )
    for i, line in enumerate(lines):
        off = REC_LINES + i * LINE_SIZE
        buf[off : off + LINE_SIZE] = line
    assert len(buf) == RECORD_SIZE
    return bytes(buf)


def _synthetic_prj(display_count: int = 2) -> bytes:
    """A whole synthetic ".prj": header with junk (except magic + count) plus display records."""
    header = bytearray(_non_canonical(HEADER_SIZE, 100))
    header[0 : len(MAGIC)] = MAGIC
    header[0x77] = display_count & 0xFF
    header[0x78] = (display_count >> 8) & 0xFF
    body = b"".join(
        _display_record(
            name=f"Display {j}",
            family=2,  # TRITON
            subtype=j % 2,  # 6323 / 6325
            address=0x1105 + j,
            seed=200 + j * 7,
        )
        for j in range(display_count)
    )
    return bytes(header) + body


# --- (a) lossless round-trip -----------------------------------------------


def test_read_then_write_is_byte_identical() -> None:
    data = _synthetic_prj()
    assert write_prj(read_prj(data), data) == data


def test_round_trip_with_trailing_bytes_preserved() -> None:
    # Trailing bytes beyond the last record are tolerated on read and preserved on write.
    data = _synthetic_prj() + b"\x01\x02\x03trailer"
    devices = read_prj(data)
    assert write_prj(devices, data) == data


def test_read_maps_expected_fields() -> None:
    devices = read_prj(_synthetic_prj())
    assert len(devices) == 2
    first = devices[0]
    assert first.device_type is DeviceType.TRITON_6323
    assert first.name == "Display 0"
    assert first.physical_address == "1.1.5"
    assert len(first.lines) == 8
    eis6 = first.lines[0]
    assert eis6.data_type is LineDataType.VALUE_1BYTE_EIS6
    assert eis6.text_off == "Temperatur"
    assert eis6.object_number == 5
    assert eis6.receive is True and eis6.send is False
    assert eis6.number_format is not None
    assert eis6.number_format.integer_digits == 2
    switch = first.lines[1]
    assert switch.data_type is LineDataType.SWITCH_EIS1
    assert switch.text_on == "EIN"
    assert switch.receive and switch.send and switch.alarm
    assert switch.number_format is None  # not a numeric line
    assert devices[1].device_type is DeviceType.TRITON_6325


# --- (b) editing a mapped field changes exactly those bytes -----------------


def _diff_positions(a: bytes, b: bytes) -> list[int]:
    assert len(a) == len(b)
    return [i for i in range(len(a)) if a[i] != b[i]]


def test_edit_name_changes_only_the_name_bytes() -> None:
    data = _synthetic_prj()
    devices = read_prj(data)
    devices[
        0
    ].name = "Kitchen"  # same length region is overwritten; tail junk preserved
    out = write_prj(devices, data)
    changed = _diff_positions(data, out)
    name_field = range(HEADER_SIZE + REC_NAME, HEADER_SIZE + REC_NAME + 41)
    assert changed  # something changed
    assert all(pos in name_field for pos in changed)


def test_edit_numeric_format_keeps_low_nibble_and_other_bytes() -> None:
    data = _synthetic_prj()
    devices = read_prj(data)
    nf = devices[0].lines[0].number_format
    assert nf is not None
    nf.show_positive_sign = True
    nf.extra_sign_position = True
    out = write_prj(devices, data)
    changed = _diff_positions(data, out)
    numfmt_pos = HEADER_SIZE + REC_LINES + 0x53
    assert changed == [numfmt_pos]  # only the numeric-format byte
    # the unresolved low nibble (0x0A on the EIS6 line) is preserved, high nibble updated
    assert out[numfmt_pos] & 0x0F == 0x0A
    assert out[numfmt_pos] & 0x60 == 0x60  # extra-sign | positive-sign bits set


def test_edit_flags_changes_only_the_flag_byte() -> None:
    data = _synthetic_prj()
    devices = read_prj(data)
    devices[0].lines[0].object_number = 9
    devices[0].lines[0].send = True
    out = write_prj(devices, data)
    flags_pos = HEADER_SIZE + REC_LINES + LN_FLAGS
    assert _diff_positions(data, out) == [flags_pos]
    assert out[flags_pos] == (9 << 3) | 0x01 | 0x02


# --- (c) malformed buffers raise PrjError -----------------------------------


def test_too_short_buffer_raises() -> None:
    with pytest.raises(PrjError):
        read_prj(b"\x00\x01\x02")


def test_bad_magic_raises() -> None:
    data = bytearray(_synthetic_prj())
    data[1] ^= 0xFF  # corrupt the magic
    with pytest.raises(PrjError):
        read_prj(bytes(data))


def test_truncated_record_raises() -> None:
    data = _synthetic_prj()
    with pytest.raises(PrjError):
        read_prj(data[: HEADER_SIZE + 10])  # declares 2 displays, body cut off


def test_unknown_family_raises() -> None:
    data = bytearray(_synthetic_prj())
    data[HEADER_SIZE + REC_FAMILY] = 9  # neither 6136UP nor TRITON
    with pytest.raises(PrjError):
        read_prj(bytes(data))


def test_unknown_line_type_raises() -> None:
    data = bytearray(_synthetic_prj())
    data[HEADER_SIZE + REC_LINES + LN_TYPE] = 0x7E  # not a documented type
    with pytest.raises(PrjError):
        read_prj(bytes(data))


def test_write_requires_header() -> None:
    with pytest.raises(PrjError):
        write_prj([], b"short")


# --- validation against a genuine LVS 2 .prj --------------------------------

from pathlib import Path  # noqa: E402

from xknxeditor.vendor.triton import read_prj as _read_prj  # noqa: E402
from xknxeditor.vendor.triton import write_prj as _write_prj  # noqa: E402

_REAL_PRJ = Path(__file__).parent / "fixtures" / "sample_triton.prj"


def test_real_lvs_prj_decodes_to_the_content_lvs_shows() -> None:
    data = _REAL_PRJ.read_bytes()
    devices = _read_prj(data)
    assert len(devices) == 4
    kinds = [d.device_type for d in devices]
    assert kinds == [
        DeviceType.INTERFACE_6136UP,
        DeviceType.TRITON_6323,
        DeviceType.TRITON_6325,
        DeviceType.TRITON_6325,
    ]
    assert [d.name for d in devices] == ["eeee", "dasdsadsa", "31232", "dasdd"]
    assert [d.physical_address for d in devices] == [
        "1.1.21",
        "1.1.23",
        "15.15.255",
        "15.15.255",
    ]
    # 6136UP display: types + texts as entered in LVS.
    up = devices[0]
    assert [(ln.data_type, ln.text_off, ln.text_on) for ln in up.lines[:6]] == [
        (LineDataType.SWITCH_EIS1, "Aus", "Ein"),
        (LineDataType.STATIC_TEXT, "statischer", ""),
        (LineDataType.VALUE_2BYTE_EIS5, "eis5", ""),
        (LineDataType.VALUE_1BYTE_EIS6, "eis6", ""),
        (LineDataType.SWITCH_EIS1, "ein", "aus"),
        (LineDataType.SWITCH_EIS1, "dd", "dasdsa"),
    ]
    assert up.lines[6].data_type is LineDataType.FREE
    # triton 6323: time + date lines (triton-only) decode.
    t3 = devices[1]
    assert t3.lines[1].data_type is LineDataType.TIME_EIS3
    assert t3.lines[2].data_type is LineDataType.DATE_EIS4


def test_real_lvs_prj_round_trips_byte_exact() -> None:
    data = _REAL_PRJ.read_bytes()
    assert _write_prj(_read_prj(data), data) == data


# --- a single-display genuine .prj whose exact LVS settings are known -------

from xknxeditor.vendor.triton.model import (  # noqa: E402
    BacklightMode,
    DateOrder,
)

_MAP_PRJ = Path(__file__).parent / "fixtures" / "sample_triton_map.prj"


def test_real_map_prj_decodes_to_the_settings_entered_in_lvs() -> None:
    """Every newly mapped field matches the settings a user entered in LVS for this file."""
    devices = _read_prj(_MAP_PRJ.read_bytes())
    assert len(devices) == 1
    device = devices[0]
    assert device.device_type is DeviceType.TRITON_6325
    assert device.name == "MAP"
    assert device.physical_address == "1.1.1"
    # device level: "Hinterleuchtung bei Störmeldung" + scroll key off.
    assert device.backlight is BacklightMode.ON_ALARM
    assert device.scroll_with_key is False

    z1, z2, z3, z4 = device.lines[0], device.lines[1], device.lines[2], device.lines[3]

    # Z1: 1-byte value (EIS6), autoscroll off / shown on receive, number format off/on/off.
    assert z1.data_type is LineDataType.VALUE_1BYTE_EIS6
    assert z1.show_on_autoscroll is False
    assert z1.show_on_receive is True
    assert z1.number_format is not None
    assert z1.number_format.first_digit_position == 10
    assert z1.number_format.integer_digits == 3
    assert z1.number_format.decimal_digits == 1
    assert z1.number_format.show_leading_zeros is False
    assert z1.number_format.extra_sign_position is True
    assert z1.number_format.show_positive_sign is False
    assert z1.number_format.round_last_digit_to_0_or_5 is False

    # Z2: 2-byte value (EIS5), same display flags, number format on/on/on.
    assert z2.data_type is LineDataType.VALUE_2BYTE_EIS5
    assert z2.show_on_autoscroll is False
    assert z2.show_on_receive is True
    assert z2.number_format is not None
    assert z2.number_format.decimal_digits == 2
    assert z2.number_format.show_leading_zeros is True
    assert z2.number_format.extra_sign_position is True
    assert z2.number_format.show_positive_sign is True

    # Z3: date line (EIS4), MM.TT.JJ ordering, autoscroll off.
    assert z3.data_type is LineDataType.DATE_EIS4
    assert z3.show_on_autoscroll is False
    assert z3.time_date_format is not None
    assert z3.time_date_format.date_order is DateOrder.MONTH_DAY_YEAR

    # Z4: switch (EIS1) alarm line, shown on receive, object 0.
    assert z4.data_type is LineDataType.SWITCH_EIS1
    assert z4.text_off == "OK"
    assert z4.text_on == "ALARM"
    assert z4.show_on_autoscroll is False
    assert z4.show_on_receive is True
    assert z4.receive is True
    assert z4.alarm is True
    assert z4.object_number == 0


def test_real_map_prj_round_trips_byte_exact() -> None:
    data = _MAP_PRJ.read_bytes()
    assert _write_prj(_read_prj(data), data) == data


@pytest.mark.parametrize("value", ["16.1.1", "1.16.1", "1.1.256", "-1.1.1", "bad", ""])
def test_writer_rejects_invalid_address_instead_of_wrapping(value: str) -> None:
    data = _MAP_PRJ.read_bytes()
    devices = read_prj(data)
    devices[0].physical_address = value
    with pytest.raises(PrjError):
        write_prj(devices, data)


@pytest.mark.parametrize("value", ["x" * 41, "雪"])
def test_writer_rejects_lossy_text(value: str) -> None:
    data = _MAP_PRJ.read_bytes()
    devices = read_prj(data)
    devices[0].name = value
    with pytest.raises(PrjError):
        write_prj(devices, data)


def test_reader_rejects_overlong_shortstring() -> None:
    data = bytearray(_MAP_PRJ.read_bytes())
    data[HEADER_SIZE + REC_NAME] = 41
    with pytest.raises(PrjError):
        read_prj(bytes(data))


@pytest.mark.parametrize("value", [-1, 32])
def test_writer_rejects_object_index_overflow(value: int) -> None:
    data = _MAP_PRJ.read_bytes()
    devices = read_prj(data)
    devices[0].lines[0].object_number = value
    with pytest.raises(PrjError):
        write_prj(devices, data)


def test_writer_rejects_numeric_format_overflow() -> None:
    data = _MAP_PRJ.read_bytes()
    devices = read_prj(data)
    fmt = devices[0].lines[0].number_format
    assert fmt is not None
    fmt.first_digit_position = 256
    with pytest.raises(PrjError):
        write_prj(devices, data)


def _mapped_view(device: object) -> tuple[object, ...]:
    """The fields blank_prj/read_prj carry, for comparing an export against its source."""
    from xknxeditor.vendor.triton.model import DisplayDevice

    assert isinstance(device, DisplayDevice)
    return (
        device.device_type,
        device.name,
        device.physical_address,
        device.backlight if device.device_type.is_triton else None,
        device.scroll_with_key if device.device_type.is_triton else None,
        tuple(
            (
                ln.data_type,
                ln.text_off,
                ln.text_on,
                ln.object_number,
                ln.receive,
                ln.send,
                ln.alarm,
                ln.show_on_autoscroll,
                ln.show_on_receive,
            )
            for ln in device.lines
        ),
    )


def test_blank_prj_round_trips_every_mapped_field() -> None:
    # A configuration authored without a source file (blank_prj) re-reads to the same mapped
    # content it was built from, for every display in the real multi-display fixture.
    from xknxeditor.vendor.triton import blank_prj

    source = _read_prj(_REAL_PRJ.read_bytes())
    out = blank_prj(source)
    assert out[: len(MAGIC)] == MAGIC
    reread = _read_prj(out)
    assert [_mapped_view(d) for d in reread] == [_mapped_view(d) for d in source]


def test_blank_prj_is_not_an_lvs_byte_copy() -> None:
    # It is honest about not reproducing the unmapped bytes: a real LVS file and the blank build of
    # its own decoded content differ (dates, option words, capacity tails are zero in the blank).
    from xknxeditor.vendor.triton import blank_prj

    data = _REAL_PRJ.read_bytes()
    assert blank_prj(_read_prj(data)) != data


def test_blank_prj_rejects_more_than_u16_displays() -> None:
    from xknxeditor.vendor.triton import blank_prj
    from xknxeditor.vendor.triton.model import DeviceType, DisplayDevice

    one = DisplayDevice(device_type=DeviceType.TRITON_6325)
    with pytest.raises(PrjError, match="too many displays"):
        blank_prj([one] * 65536)


def test_blank_prj_numeric_line_without_format_still_re_reads() -> None:
    # A numeric line authored without an explicit NumberFormat must export to a file that re-reads
    # (zero digit bytes would be rejected on import).
    from xknxeditor.vendor.triton import blank_prj, read_prj
    from xknxeditor.vendor.triton.model import (
        DeviceType,
        DisplayDevice,
        DisplayLine,
        LineDataType,
    )

    device = DisplayDevice(
        device_type=DeviceType.TRITON_6325,
        physical_address="1.1.5",
        lines=[
            DisplayLine(
                index=1, data_type=LineDataType.VALUE_2BYTE_EIS5, number_format=None
            )
        ],
    )
    out = read_prj(blank_prj([device]))[0]
    fmt = out.lines[0].number_format
    assert fmt is not None
    assert fmt.first_digit_position >= 1 and fmt.integer_digits >= 1


def test_alarm_tab_decodes_from_real_fixtures() -> None:
    # The alarm (Störmeldung) tab decodes to the values LVS stored, cross-checked against the
    # binary-derived offsets (6136UP: duration word in seconds; TRITON: byte 0x36 bit fields).
    devices = _read_prj(_REAL_PRJ.read_bytes())
    up, t1, t2, t3 = devices
    assert up.alarm_tone_duration_s == 18000  # 5 Stunden
    assert t1.alarm_tone_duration_s == 0 and t1.key_backlight_blinks_on_alarm is True
    assert t1.alarm_scroll_after_3s is False
    assert t2.alarm_tone_duration_s == 1 and t2.key_backlight_blinks_on_alarm is False
    assert t3.alarm_tone_duration_s == 3600  # 1 Stunde

    (mapped,) = _read_prj(_MAP_PRJ.read_bytes())
    assert mapped.alarm_tone_duration_s == 1
    assert mapped.alarm_scroll_after_3s is False
    assert mapped.key_backlight_blinks_on_alarm is False


def test_alarm_tab_edits_round_trip_and_keep_other_option_bits() -> None:
    # Editing a TRITON alarm setting rewrites only its bits; the low three bits of 0x36 and the
    # high byte 0x37 (other/unresolved options) survive, so unrelated state is never lost.
    from xknxeditor.vendor.triton.model import ALARM_TONE_DURATIONS_TRITON

    data = bytearray(_MAP_PRJ.read_bytes())
    rec = HEADER_SIZE
    data[rec + 0x36] = 0x05  # set two spare low-bits that we must preserve
    data[rec + 0x37] = 0x99  # high byte must survive verbatim
    device = read_prj(bytes(data))[0]
    device.alarm_tone_duration_s = 3600  # 1 h -> index 6 (0xC0)
    device.alarm_scroll_after_3s = True  # bit 0x08
    device.key_backlight_blinks_on_alarm = True  # bit 0x10
    out = write_prj([device], bytes(data))
    assert out[rec + 0x36] == (0x05 | 0xC0 | 0x08 | 0x10)
    assert out[rec + 0x37] == 0x99
    back = read_prj(out)[0]
    assert back.alarm_tone_duration_s == 3600
    assert back.alarm_scroll_after_3s is True
    assert back.key_backlight_blinks_on_alarm is True
    assert ALARM_TONE_DURATIONS_TRITON.index(3600) == 6


def test_backlight_beep_scroll_decode_from_fixtures() -> None:
    # Backlight (per-family enum), 6136UP keypress tone and TRITON scroll-with-key decode to the
    # binary-verified values.
    from xknxeditor.vendor.triton.model import BacklightMode

    up, t1, t2, t3 = _read_prj(_REAL_PRJ.read_bytes())
    assert up.backlight is BacklightMode.ON_RECEIVE and up.beep_on_keypress is True
    assert t1.backlight is BacklightMode.ON_RECEIVE
    assert t2.backlight is BacklightMode.LIKE_TEXT_FIELDS
    assert t2.scroll_with_key is True  # 0x36 bit 0 set on this display
    assert t3.backlight is BacklightMode.ALWAYS_ON
    assert t1.scroll_with_key is False
    (mapped,) = _read_prj(_MAP_PRJ.read_bytes())
    assert mapped.backlight is BacklightMode.ON_ALARM


def test_line_scaling_send_weekday_decode_from_fixtures() -> None:
    from xknxeditor.vendor.triton.model import LineDataType, SendModeEis1

    up, t1, _t2, _t3 = _read_prj(_REAL_PRJ.read_bytes())
    # EIS6 scaling endpoints (line 4 of the 6136UP; line 5 of the first TRITON).
    assert up.lines[3].scaling is not None
    assert (up.lines[3].scaling.display_at_0, up.lines[3].scaling.display_at_255) == (
        0,
        255,
    )
    assert (t1.lines[4].scaling.display_at_0, t1.lines[4].scaling.display_at_255) == (
        1,
        360,
    )
    # EIS1 send modes on the 6136UP's switch lines 5 and 6.
    assert up.lines[4].send_parameters.eis1_mode is SendModeEis1.TOGGLE
    assert up.lines[5].send_parameters.eis1_mode is SendModeEis1.OFF
    # EIS5 default send bounds decode as the EIS5 float -20.48 / 20.47.
    eis5 = next(ln for ln in up.lines if ln.data_type is LineDataType.VALUE_2BYTE_EIS5)
    assert eis5.send_parameters.eis5_min == -20.48
    assert eis5.send_parameters.eis5_max == 20.47
    # Weekday on the first TRITON's time line (line 2): shown, German.
    time_line = t1.lines[1]
    assert time_line.data_type is LineDataType.TIME_EIS3
    assert time_line.time_date_format.show_weekday is True
    assert time_line.time_date_format.weekday_in_english is False
    # EIS6 send object bytes on the map fixture's scaled line.
    (mapped,) = _read_prj(_MAP_PRJ.read_bytes())
    eis6 = mapped.lines[0]
    assert (eis6.scaling.display_at_0, eis6.scaling.display_at_255) == (0, 1000)
    assert eis6.send_parameters.eis6_min == 0 and eis6.send_parameters.eis6_max == 254


def test_new_field_edits_round_trip() -> None:
    # Editing the newly mapped fields and writing survives a re-read (values, not raw bytes).
    from xknxeditor.vendor.triton.model import BacklightMode, SendModeEis1

    data = _REAL_PRJ.read_bytes()
    up, t1, _t2, _t3 = _read_prj(data)
    up.beep_on_keypress = False
    up.backlight = BacklightMode.ALWAYS_OFF
    up.lines[3].scaling.display_at_0 = -50
    up.lines[3].scaling.display_at_255 = 450
    up.lines[4].send_parameters.eis1_mode = SendModeEis1.ON
    t1.backlight = BacklightMode.ON_ALARM
    t1.scroll_with_key = True
    t1.lines[1].time_date_format.weekday_in_english = True
    out = write_prj([up, t1], data)
    up2, t1b, *_ = _read_prj(out)
    assert up2.beep_on_keypress is False and up2.backlight is BacklightMode.ALWAYS_OFF
    assert (up2.lines[3].scaling.display_at_0, up2.lines[3].scaling.display_at_255) == (
        -50,
        450,
    )
    assert up2.lines[4].send_parameters.eis1_mode is SendModeEis1.ON
    assert t1b.backlight is BacklightMode.ON_ALARM and t1b.scroll_with_key is True
    assert t1b.lines[1].time_date_format.weekday_in_english is True


def test_eis5_float_codec_round_trips_sample_values() -> None:
    from xknxeditor.vendor.triton.prj import _eis5_decode, _eis5_encode

    for value in (0.0, -20.48, 20.47, 1.0, -1.0, 12.34, -5.0, 100.0):
        assert _eis5_decode(_eis5_encode(value)) == value
