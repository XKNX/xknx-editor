"""Reader/writer for the Busch-Jaeger LVS 2 ".prj" project file ("EIB LCD Projektdatei").

VALIDATED against a genuine LVS 2 ".prj" (``tests/fixtures/sample_triton.prj``, saved by the real
program): the decoded displays/lines/types/texts match what LVS shows and the file round-trips
byte-exact, so the framing and the mapped fields below are confirmed against real output. The
layout itself was recovered by static reverse-engineering of the LVS 2 tool; per-field offsets live
in named module constants so a future correction is a one-line change. Fields the analysis did not
resolve are preserved verbatim (below), so an unmodelled byte never breaks the round-trip.

Unlike the device payload (which LVS stores six-bit packed), the ".prj" is an UNCOMPRESSED binary
dump: a 121-byte header, then one 965-byte record per display, each holding eight 113-byte line
records. No 6-bit codec is involved here.

Honest, lossless contract: this reader maps the fields for which the analysis gives a concrete
offset AND an invertible encoding. Everything else (dates, status words, unresolved option bits) is
preserved verbatim. :func:`write_prj` therefore requires the original bytes and patches only the
mapped field bytes back into them, so a real ".prj" round-trips byte-identically even where we
understand only part of it.

Mapped fields (per display): name, physical address, device family/subtype, the backlight mode
(a per-family enum in the display-flags byte), the keypress tone (6136UP), the scroll-with-key flag
(TRITON), and the alarm (Störmeldung) tab — signal-tone duration, alarm scroll interval and
key-backlight blink. Per line: the two texts, the data type, the numeric format, the
receive/send/alarm flags with the object index, the autoscroll/show-on-receive flags, the date
order (date line) or weekday options (time line), the EIS6 display scaling (slope/offset int16s),
and the send-tab values (EIS1 mode, EIS5 min/max 2-byte floats, EIS6 min/max object bytes).
Preserved verbatim: ShortString capacity tails, modification dates, the status byte, unresolved
option bits, and every byte the analysis left unresolved. The EIS6 scaling endpoints round-trip
losslessly for spans up to 32639 display units; wider spans quantise, as they do in LVS itself.
"""

from __future__ import annotations

from xknxeditor.vendor.triton.model import (
    ALARM_TONE_DURATIONS_TRITON,
    BacklightMode,
    DateOrder,
    DeviceType,
    DisplayDevice,
    DisplayLine,
    Eis6Scaling,
    LineDataType,
    NumberFormat,
    SendModeEis1,
    SendParameters,
    TimeDateFormat,
)

# --- framing ---------------------------------------------------------------

# Delphi ShortString[20]: one length byte 0x14 then the 20 ASCII bytes.
MAGIC = b"\x14EIB LCD Projektdatei"
HEADER_SIZE = 0x79  # 121
RECORD_SIZE = 0x3C5  # 965 bytes per display
LINE_SIZE = 0x71  # 113 bytes per line record
LINE_COUNT = 8  # the disk always stores eight line slots per display

# A ShortString[40] occupies 41 bytes: one length byte then 40 bytes of capacity. Only the first
# `length` capacity bytes are the text; the rest is unused and need not be zero, so it is preserved.
SHORTSTR40_SIZE = 41
SHORTSTR40_CAP = 40

# --- header offsets (from the file start) ----------------------------------

# Only the magic and the display count drive parsing; the rest are documented for a future change
# but are preserved verbatim (not mapped into the model).
HDR_MAGIC = 0x00  # ShortString[20] magic, 21 bytes
HDR_DESCRIPTION = 0x15  # ShortString[40] project description
HDR_AUTHOR = 0x3E  # ShortString[40] edited-by/author
HDR_CREATED = 0x67  # IEEE-754 double creation date/time
HDR_MODIFIED = 0x6F  # IEEE-754 double last-save date/time
HDR_DISPLAY_COUNT = 0x77  # uint16 display count

# --- display-record offsets (relative to the record start) -----------------

REC_NAME = 0x00  # ShortString[40] display description -> DisplayDevice.name
REC_MODIFIED = 0x29  # double modification date/time (preserved)
REC_STATUS = 0x31  # programmed/status byte (preserved)
REC_ADDRESS = 0x32  # uint16 physical address -> DisplayDevice.physical_address
REC_FAMILY = 0x34  # 1=6136UP, 2=TRITON, 0=legacy 6103 (rejected)
REC_SUBTYPE = 0x35  # TRITON only: 0=6323, 1=6325
REC_OPTIONS = 0x36  # alarm (Störmeldung) tab word; mapped per family (see below)
REC_FLAGS = (
    0x38  # display flags: TRITON backlight/scroll bits mapped, the rest preserved
)
REC_LINES = 0x39  # first of the eight line records
REC_TRAILER = 0x3C1  # four unresolved trailing bytes (preserved)

# REC_OPTIONS (0x36..0x37) holds the alarm tab. For a 6136UP the whole little-endian word is the
# signal-tone duration in seconds (0 = unlimited). For a TRITON only the low byte is used: bits 5..7
# a signal-tone index into ALARM_TONE_DURATIONS_TRITON, bit 3 the 3 s (vs 1 s) alarm scroll, bit 4
# the key-backlight blink; its low three bits and the whole high byte are other options, preserved.
REC_ALARM_TONE_TRITON = 0xE0
REC_ALARM_TONE_SHIFT = 5
REC_ALARM_SCROLL_3S = 0x08
REC_ALARM_KEY_BLINK = 0x10

# --- line-record offsets (relative to the line start) -----------------------

LN_TEXT_OFF = 0x00  # ShortString[40] Off/value/static text -> DisplayLine.text_off
LN_TEXT_ON = 0x29  # ShortString[40] On text -> DisplayLine.text_on
LN_TYPE = 0x52  # data type byte
LN_NUMFMT = 0x53  # numeric-format flags in the high nibble; low nibble preserved
LN_FIRST_POS = 0x54  # first digit position (one-based)
LN_INT_DIGITS = 0x55  # integer digits
LN_DEC_DIGITS = 0x56  # decimal digits
LN_COEFF = (
    0x57  # int16 EIS6 slope coefficient (with LN_OFFSET encodes the display scaling)
)
LN_OFFSET = 0x59  # int16 EIS6 display-at-zero offset
LN_FLAGS = 0x5F  # receive/send/alarm flags + object index
LN_UNK_60 = 0x60  # unresolved (preserved)
LN_DISPLAY = (
    0x61  # autoscroll/show-on-receive bits mapped; higher option bits preserved
)
LN_TIMEDATE = (
    0x62  # date order mapped on a date line; weekday bits / whole time byte preserved
)
LN_EIS1_META = 0x63  # EIS1 device-metadata bits (preserved)
LN_CAP = 0x70  # first line only: cached capacity (preserved)

# --- line type byte values --------------------------------------------------

TYPE_FREE = 0xFF
TYPE_EIS1 = 0x00
TYPE_EIS6 = 0x01
TYPE_EIS5 = 0x02
TYPE_STATIC = 0x03
TYPE_TIME = 0x04
TYPE_DATE = 0x05

_TYPE_TO_DATA: dict[int, LineDataType] = {
    TYPE_FREE: LineDataType.FREE,
    TYPE_EIS1: LineDataType.SWITCH_EIS1,
    TYPE_EIS6: LineDataType.VALUE_1BYTE_EIS6,
    TYPE_EIS5: LineDataType.VALUE_2BYTE_EIS5,
    TYPE_STATIC: LineDataType.STATIC_TEXT,
    TYPE_TIME: LineDataType.TIME_EIS3,
    TYPE_DATE: LineDataType.DATE_EIS4,
}
_DATA_TO_TYPE: dict[LineDataType, int] = {v: k for k, v in _TYPE_TO_DATA.items()}

# --- family / subtype values ------------------------------------------------

FAMILY_LEGACY = 0
FAMILY_6136UP = 1
FAMILY_TRITON = 2
SUBTYPE_6323 = 0
SUBTYPE_6325 = 1

# --- bit masks --------------------------------------------------------------

LN_FLAG_RECEIVE = 0x01
LN_FLAG_SEND = 0x02
LN_FLAG_ALARM = 0x04
LN_OBJECT_SHIFT = 3
LN_OBJECT_MASK = 0x1F  # object index lives in bits 3..7

NUMFMT_LEADING_ZEROS = 0x10
NUMFMT_EXTRA_SIGN = 0x20
NUMFMT_POSITIVE_SIGN = 0x40
NUMFMT_ROUND_0_5 = 0x80
NUMFMT_LOW_NIBBLE = 0x0F  # low nibble is unresolved and preserved

# REC_FLAGS (0x38) high bits carry the backlight and, on a 6136UP, the keypress tone; the low bits
# are preserved. Backlight is a small enum, encoded differently per family. TRITON scroll-with-key
# lives in bit 0 of REC_OPTIONS (0x36), not here.
REC_FLAGS_BACKLIGHT_TRITON = 0x30  # TRITON backlight at bits 4..5
REC_FLAGS_BACKLIGHT_6136UP = 0x60  # 6136UP backlight at bits 5..6
REC_FLAGS_BEEP_6136UP = 0x10  # 6136UP "Ton bei Tastendruck"
REC_SCROLL_KEY_TRITON = 0x01  # TRITON scroll-with-key: bit 0 of REC_OPTIONS

_BACKLIGHT_TRITON: dict[int, BacklightMode] = {
    0x00: BacklightMode.ALWAYS_ON,
    0x10: BacklightMode.ON_RECEIVE,
    0x20: BacklightMode.LIKE_TEXT_FIELDS,
    0x30: BacklightMode.ON_ALARM,
}
_BACKLIGHT_6136UP: dict[int, BacklightMode] = {
    0x00: BacklightMode.ALWAYS_OFF,
    0x20: BacklightMode.ALWAYS_ON,
    0x40: BacklightMode.ON_RECEIVE,
}
_BACKLIGHT_TRITON_INV = {v: k for k, v in _BACKLIGHT_TRITON.items()}
_BACKLIGHT_6136UP_INV = {v: k for k, v in _BACKLIGHT_6136UP.items()}

# LN_DISPLAY holds the per-line autoscroll / show-on-receive choices. The two mapped bits are both
# set in every ground-truth line (autoscroll off, shown on receive); bits 0x40/0x80 are further
# display options left unresolved and preserved.
LN_DISP_AUTOSCROLL_OFF = 0x10  # set -> show_on_autoscroll is False
LN_DISP_SHOW_ON_RECEIVE = 0x20  # set -> show_on_receive is True

# LN_TIMEDATE top bit selects the date order on a date line; on a TIME line bit 0x20 shows the
# weekday and bit 0x80 switches it to English. Other bits are preserved.
LN_TD_DATE_MONTH_FIRST = (
    0x80  # date line: set -> MONTH_DAY_YEAR, clear -> DAY_MONTH_YEAR
)
LN_TD_WEEKDAY = 0x20  # time line: set -> show the weekday
LN_TD_WEEKDAY_ENGLISH = 0x80  # time line: set -> weekday in English, clear -> German

# Send-tab bytes in the 0x5B..0x5E range, meaning them per the line's data type. An EIS1 line keeps
# its send mode at 0x5C; an EIS5 line two 16-bit words split noncontiguously; an EIS6 line two raw
# object bytes. Only a sending (6136UP) line uses these, but they are stored on every line of the
# type, so they are mapped unconditionally and round-trip.
LN_EIS1_MODE = 0x5C  # EIS1 send mode byte (0 toggle, 1 on, 2 off)
LN_EIS5_MIN_LO = 0x5C
LN_EIS5_MIN_HI = 0x5D
LN_EIS5_MAX_LO = 0x5B
LN_EIS5_MAX_HI = 0x5E
LN_EIS6_SEND_MIN = 0x5C  # raw object value 0..255
LN_EIS6_SEND_MAX = 0x5B  # raw object value 0..255

_EIS1_MODE_TO_CODE = {SendModeEis1.TOGGLE: 0, SendModeEis1.ON: 1, SendModeEis1.OFF: 2}
_EIS1_CODE_TO_MODE = {v: k for k, v in _EIS1_MODE_TO_CODE.items()}


class PrjError(ValueError):
    """Raised when a buffer is not a structurally valid LVS 2 ".prj" file."""


# --- small byte helpers -----------------------------------------------------


def _read_u16(data: bytes, offset: int) -> int:
    return data[offset] | (data[offset + 1] << 8)


def _write_u16(buf: bytearray, offset: int, value: int) -> None:
    buf[offset] = value & 0xFF
    buf[offset + 1] = (value >> 8) & 0xFF


def _read_s16(data: bytes, offset: int) -> int:
    value = _read_u16(data, offset)
    return value - 0x10000 if value & 0x8000 else value


def _eis5_decode(word: int) -> float:
    """Decode a KNX 2-byte float (EIS 5) word into its physical value."""
    exponent = (word >> 11) & 0x0F
    mantissa = (word & 0x07FF) - (0x0800 if word & 0x8000 else 0)
    return mantissa * (2**exponent) / 100.0


def _eis5_encode(value: float) -> int:
    """Encode a physical value as a KNX 2-byte float (EIS 5) word.

    Produces a word that decodes back to ``value`` (within the format's precision); it need not be
    the exact bit pattern a given tool would choose. Mirrors the device's round-to-nearest-even
    mantissa and the exponent search.
    """
    scaled = value * 100.0
    exponent = 0
    # Narrow the mantissa into the signed 11-bit range by raising the exponent.
    while scaled < -2048.0 or scaled > 2047.0:
        scaled /= 2.0
        exponent += 1
        if exponent > 15:
            raise PrjError("value out of range for the EIS 5 float encoding")
    mantissa = round(scaled)  # banker's rounding, like the device
    word = (exponent & 0x0F) << 11
    if mantissa < 0:
        word |= 0x8000
        word |= (mantissa + 0x0800) & 0x07FF
    else:
        word |= mantissa & 0x07FF
    return word


def _read_shortstring(data: bytes, offset: int, capacity: int) -> str:
    length = data[offset]
    if length > capacity:
        raise PrjError("ShortString length exceeds its capacity")
    return data[offset + 1 : offset + 1 + length].decode("latin-1")


def _patch_shortstring(buf: bytearray, offset: int, capacity: int, value: str) -> None:
    """Write the length byte and characters, leaving the unused capacity tail verbatim."""
    try:
        raw = value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise PrjError("text cannot be encoded as Latin-1") from exc
    if len(raw) > capacity:
        raise PrjError("text exceeds its ShortString capacity")
    buf[offset] = len(raw)
    buf[offset + 1 : offset + 1 + len(raw)] = raw


def _format_address(word: int) -> str:
    return f"{(word >> 12) & 0xF}.{(word >> 8) & 0xF}.{word & 0xFF}"


def _parse_address(text: str) -> int:
    parts = text.split(".")
    if len(parts) != 3 or any(not p.isascii() or not p.isdecimal() for p in parts):
        raise PrjError(f"invalid physical address: {text!r}")
    area, line, device = (int(p) for p in parts)
    if not (0 <= area <= 15 and 0 <= line <= 15 and 0 <= device <= 255):
        raise PrjError(f"invalid physical address: {text!r}")
    return (area << 12) | (line << 8) | device


def _family_subtype_for(device_type: DeviceType) -> tuple[int, int]:
    if device_type is DeviceType.INTERFACE_6136UP:
        return FAMILY_6136UP, SUBTYPE_6323
    if device_type is DeviceType.TRITON_6323:
        return FAMILY_TRITON, SUBTYPE_6323
    return FAMILY_TRITON, SUBTYPE_6325


def _device_type_for(family: int, subtype: int) -> DeviceType:
    if family == FAMILY_6136UP:
        return DeviceType.INTERFACE_6136UP
    if family == FAMILY_TRITON:
        if subtype == SUBTYPE_6323:
            return DeviceType.TRITON_6323
        if subtype == SUBTYPE_6325:
            return DeviceType.TRITON_6325
        raise PrjError(f"unknown TRITON subtype {subtype} at display record")
    raise PrjError(
        f"unsupported display family {family} (only 6136UP and TRITON are mapped)"
    )


# --- offsets ----------------------------------------------------------------


def _record_offset(index: int) -> int:
    return HEADER_SIZE + index * RECORD_SIZE


def _line_offset(record_offset: int, slot: int) -> int:
    return record_offset + REC_LINES + slot * LINE_SIZE


# --- reader -----------------------------------------------------------------


def read_prj(data: bytes) -> list[DisplayDevice]:
    """Parse an LVS 2 ".prj" file into one :class:`DisplayDevice` per display record.

    Maps only the fields the layout analysis pins down concretely (see the module docstring);
    unmapped bytes are ignored here and preserved by :func:`write_prj`. All eight on-disk line
    slots become :class:`DisplayLine` entries (index 1..8) regardless of device type, so the list
    mirrors the file one-to-one.

    Raises :class:`PrjError` if the buffer is too short, the magic does not match, the file is
    truncated relative to its declared display count, or a record carries an unmapped
    family/subtype/line-type byte. Trailing bytes beyond the last record are tolerated.
    """
    if len(data) < HEADER_SIZE:
        raise PrjError("file shorter than the 121-byte header")
    if data[: len(MAGIC)] != MAGIC:
        raise PrjError("bad framing: missing 'EIB LCD Projektdatei' magic")

    count = _read_u16(data, HDR_DISPLAY_COUNT)
    required = HEADER_SIZE + count * RECORD_SIZE
    if len(data) < required:
        raise PrjError(
            f"file truncated: need {required} bytes for {count} displays, got {len(data)}"
        )

    return [_read_record(data, _record_offset(j)) for j in range(count)]


def _read_record(data: bytes, rec: int) -> DisplayDevice:
    family = data[rec + REC_FAMILY]
    subtype = data[rec + REC_SUBTYPE]
    device_type = _device_type_for(family, subtype)
    name = _read_shortstring(data, rec + REC_NAME, SHORTSTR40_CAP)
    address = _format_address(_read_u16(data, rec + REC_ADDRESS))
    lines = [_read_line(data, _line_offset(rec, i), i) for i in range(LINE_COUNT)]
    device = DisplayDevice(
        device_type=device_type,
        name=name,
        physical_address=address,
        lines=lines,
    )
    # The backlight enum is stored in the high bits of REC_FLAGS, encoded differently per family;
    # a 6136UP additionally keeps the keypress tone there, while TRITON keeps scroll-with-key in
    # REC_OPTIONS. Both families carry the alarm signal-tone duration in REC_OPTIONS.
    flags = data[rec + REC_FLAGS]
    options = data[rec + REC_OPTIONS]
    if device_type.is_triton:
        device.backlight = _BACKLIGHT_TRITON[flags & REC_FLAGS_BACKLIGHT_TRITON]
        device.scroll_with_key = bool(options & REC_SCROLL_KEY_TRITON)
        device.alarm_tone_duration_s = ALARM_TONE_DURATIONS_TRITON[
            (options & REC_ALARM_TONE_TRITON) >> REC_ALARM_TONE_SHIFT
        ]
        device.alarm_scroll_after_3s = bool(options & REC_ALARM_SCROLL_3S)
        device.key_backlight_blinks_on_alarm = bool(options & REC_ALARM_KEY_BLINK)
    else:
        device.backlight = _BACKLIGHT_6136UP[flags & REC_FLAGS_BACKLIGHT_6136UP]
        device.beep_on_keypress = bool(flags & REC_FLAGS_BEEP_6136UP)
        # A 6136UP stores the signal-tone duration as the whole option word, in seconds.
        device.alarm_tone_duration_s = _read_u16(data, rec + REC_OPTIONS)
    return device


def _read_line(data: bytes, ln: int, slot: int) -> DisplayLine:
    type_byte = data[ln + LN_TYPE]
    data_type = _TYPE_TO_DATA.get(type_byte)
    if data_type is None:
        raise PrjError(f"unknown line type byte {type_byte:#04x} at line slot {slot}")
    flags = data[ln + LN_FLAGS]
    disp = data[ln + LN_DISPLAY]
    number_format: NumberFormat | None = None
    if data_type.is_number:
        nf = data[ln + LN_NUMFMT]
        number_format = NumberFormat(
            first_digit_position=data[ln + LN_FIRST_POS],
            integer_digits=data[ln + LN_INT_DIGITS],
            decimal_digits=data[ln + LN_DEC_DIGITS],
            show_leading_zeros=bool(nf & NUMFMT_LEADING_ZEROS),
            extra_sign_position=bool(nf & NUMFMT_EXTRA_SIGN),
            show_positive_sign=bool(nf & NUMFMT_POSITIVE_SIGN),
            round_last_digit_to_0_or_5=bool(nf & NUMFMT_ROUND_0_5),
        )
    time_date_format: TimeDateFormat | None = None
    if data_type is LineDataType.DATE_EIS4:
        date_order = (
            DateOrder.MONTH_DAY_YEAR
            if data[ln + LN_TIMEDATE] & LN_TD_DATE_MONTH_FIRST
            else DateOrder.DAY_MONTH_YEAR
        )
        time_date_format = TimeDateFormat(date_order=date_order)
    elif data_type is LineDataType.TIME_EIS3:
        td = data[ln + LN_TIMEDATE]
        time_date_format = TimeDateFormat(
            show_weekday=bool(td & LN_TD_WEEKDAY),
            weekday_in_english=bool(td & LN_TD_WEEKDAY_ENGLISH),
        )
    scaling: Eis6Scaling | None = None
    if data_type is LineDataType.VALUE_1BYTE_EIS6:
        scaling = _read_eis6_scaling(data, ln)
    return DisplayLine(
        index=slot + 1,
        data_type=data_type,
        text_off=_read_shortstring(data, ln + LN_TEXT_OFF, SHORTSTR40_CAP),
        text_on=_read_shortstring(data, ln + LN_TEXT_ON, SHORTSTR40_CAP),
        # The object index occupies bits 3..7 of the flags byte for every line; it is 0 when the
        # line has no object, and it is kept verbatim so the flags byte round-trips exactly.
        object_number=flags >> LN_OBJECT_SHIFT,
        receive=bool(flags & LN_FLAG_RECEIVE),
        send=bool(flags & LN_FLAG_SEND),
        alarm=bool(flags & LN_FLAG_ALARM),
        show_on_autoscroll=not bool(disp & LN_DISP_AUTOSCROLL_OFF),
        show_on_receive=bool(disp & LN_DISP_SHOW_ON_RECEIVE),
        number_format=number_format,
        scaling=scaling,
        time_date_format=time_date_format,
        send_parameters=_read_send_parameters(data, ln, data_type),
    )


def _read_eis6_scaling(data: bytes, ln: int) -> Eis6Scaling:
    """Decode the EIS6 display scaling from the line's slope/offset int16s (see module notes)."""
    c = _read_s16(data, ln + LN_COEFF)
    o = _read_s16(data, ln + LN_OFFSET)
    display_at_255 = o + int(255 * c / 256)  # truncate toward zero
    return Eis6Scaling(display_at_0=o, display_at_255=display_at_255)


def _read_send_parameters(
    data: bytes, ln: int, data_type: LineDataType
) -> SendParameters | None:
    """Decode the send-tab bytes a line of this type carries (0x5B..0x5E / EIS1 mode byte)."""
    if data_type is LineDataType.SWITCH_EIS1:
        return SendParameters(eis1_mode=_EIS1_CODE_TO_MODE.get(data[ln + LN_EIS1_MODE]))
    if data_type is LineDataType.VALUE_2BYTE_EIS5:
        wmin = data[ln + LN_EIS5_MIN_LO] | (data[ln + LN_EIS5_MIN_HI] << 8)
        wmax = data[ln + LN_EIS5_MAX_LO] | (data[ln + LN_EIS5_MAX_HI] << 8)
        return SendParameters(eis5_min=_eis5_decode(wmin), eis5_max=_eis5_decode(wmax))
    if data_type is LineDataType.VALUE_1BYTE_EIS6:
        return SendParameters(
            eis6_min=data[ln + LN_EIS6_SEND_MIN], eis6_max=data[ln + LN_EIS6_SEND_MAX]
        )
    return None


# --- writer -----------------------------------------------------------------


def blank_prj(devices: list[DisplayDevice]) -> bytes:
    """Build a structurally valid ".prj" from ``devices`` without an original template.

    Used to export a configuration that was authored in the editor (no source ".prj" to patch).
    The framing, display count and every mapped field are filled in; every byte the layout analysis
    left unresolved (dates, status/option words, coefficients, ShortString capacity tails) is zero.
    The file therefore re-reads to the same displays but is not a byte-copy of an LVS file, so the
    device payload it would ultimately drive stays experimental, like :mod:`.codec`.
    """
    count = len(devices)
    if count > 0xFFFF:
        raise PrjError(f"too many displays for a .prj file: {count} (max 65535)")
    buf = bytearray(HEADER_SIZE + count * RECORD_SIZE)
    buf[: len(MAGIC)] = MAGIC
    _write_u16(buf, HDR_DISPLAY_COUNT, count)
    # An unused line slot is marked free, not left as a zero type byte (0x00 is not a valid line
    # type and the reader would reject it); write_prj overwrites the slots a device actually uses.
    for j in range(count):
        rec = _record_offset(j)
        for slot in range(LINE_COUNT):
            buf[_line_offset(rec, slot) + LN_TYPE] = TYPE_FREE
    return write_prj(devices, bytes(buf))


def write_prj(devices: list[DisplayDevice], original: bytes) -> bytes:
    """Patch the mapped fields of ``devices`` back into ``original``, byte-for-byte lossless.

    Only the field bytes this module maps are overwritten; every other byte of ``original`` (dates,
    status/option words, coefficients, ShortString capacity tails, unresolved bytes, and the whole
    header except where a mapped field lives) is preserved. ``original`` is required precisely
    because field knowledge is partial: it is the only honest way to keep the unknown bytes.

    Devices are matched to records positionally, and each device's lines to the eight line slots
    positionally. Records or slots without a corresponding device/line are left untouched; devices
    beyond the file's declared display count are ignored.
    """
    if len(original) < HEADER_SIZE:
        raise PrjError("original shorter than the 121-byte header")
    if original[: len(MAGIC)] != MAGIC:
        raise PrjError("bad framing: original missing 'EIB LCD Projektdatei' magic")

    count = _read_u16(original, HDR_DISPLAY_COUNT)
    required = HEADER_SIZE + count * RECORD_SIZE
    if len(original) < required:
        raise PrjError(
            f"original truncated: need {required} bytes for {count} displays"
        )

    buf = bytearray(original)
    for j in range(count):
        if j >= len(devices):
            break
        _patch_record(buf, _record_offset(j), devices[j])
    return bytes(buf)


def _patch_record(buf: bytearray, rec: int, device: DisplayDevice) -> None:
    _patch_shortstring(buf, rec + REC_NAME, SHORTSTR40_CAP, device.name)
    word = _parse_address(device.physical_address)
    _write_u16(buf, rec + REC_ADDRESS, word)
    family, subtype = _family_subtype_for(device.device_type)
    buf[rec + REC_FAMILY] = family
    flags = buf[rec + REC_FLAGS]
    if family == FAMILY_TRITON:
        buf[rec + REC_SUBTYPE] = subtype
        flags = (flags & ~REC_FLAGS_BACKLIGHT_TRITON) | _BACKLIGHT_TRITON_INV.get(
            device.backlight, 0x00
        )
        buf[rec + REC_FLAGS] = flags
        # REC_OPTIONS (0x36) low byte: scroll-with-key (bit 0) plus the alarm-tab bits; the rest of
        # the low byte and the whole high byte are other options and stay as they were.
        options = buf[rec + REC_OPTIONS] & ~(
            REC_ALARM_TONE_TRITON
            | REC_ALARM_SCROLL_3S
            | REC_ALARM_KEY_BLINK
            | REC_SCROLL_KEY_TRITON
        )
        options |= (
            _triton_tone_index(device) << REC_ALARM_TONE_SHIFT
        ) & REC_ALARM_TONE_TRITON
        if device.alarm_scroll_after_3s:
            options |= REC_ALARM_SCROLL_3S
        if device.key_backlight_blinks_on_alarm:
            options |= REC_ALARM_KEY_BLINK
        if device.scroll_with_key:
            options |= REC_SCROLL_KEY_TRITON
        buf[rec + REC_OPTIONS] = options
    else:
        flags = (flags & ~REC_FLAGS_BACKLIGHT_6136UP) | _BACKLIGHT_6136UP_INV.get(
            device.backlight, 0x00
        )
        flags = (flags & ~REC_FLAGS_BEEP_6136UP) | (
            REC_FLAGS_BEEP_6136UP if device.beep_on_keypress else 0
        )
        buf[rec + REC_FLAGS] = flags
        # A 6136UP stores the signal-tone duration as the whole option word, in seconds.
        _write_u16(buf, rec + REC_OPTIONS, device.alarm_tone_duration_s & 0xFFFF)
    for slot in range(LINE_COUNT):
        if slot >= len(device.lines):
            break
        _patch_line(buf, _line_offset(rec, slot), device.lines[slot])


def _triton_tone_index(device: DisplayDevice) -> int:
    """The TRITON signal-tone 3-bit index for a device's duration (0 for an unknown value)."""
    try:
        return ALARM_TONE_DURATIONS_TRITON.index(device.alarm_tone_duration_s)
    except ValueError:
        return 0


def _patch_line(buf: bytearray, ln: int, line: DisplayLine) -> None:
    _patch_shortstring(buf, ln + LN_TEXT_OFF, SHORTSTR40_CAP, line.text_off)
    _patch_shortstring(buf, ln + LN_TEXT_ON, SHORTSTR40_CAP, line.text_on)
    buf[ln + LN_TYPE] = _DATA_TO_TYPE[line.data_type]
    fmt = line.number_format
    if fmt is None and line.data_type.is_number:
        # A numeric line authored in the editor may carry no explicit format yet; write valid
        # defaults so the exported file re-reads (zero digit bytes are rejected on import).
        fmt = NumberFormat()
    if fmt is not None:
        nf = buf[ln + LN_NUMFMT] & NUMFMT_LOW_NIBBLE  # keep the unresolved low nibble
        if fmt.show_leading_zeros:
            nf |= NUMFMT_LEADING_ZEROS
        if fmt.extra_sign_position:
            nf |= NUMFMT_EXTRA_SIGN
        if fmt.show_positive_sign:
            nf |= NUMFMT_POSITIVE_SIGN
        if fmt.round_last_digit_to_0_or_5:
            nf |= NUMFMT_ROUND_0_5
        buf[ln + LN_NUMFMT] = nf
        for offset, value in (
            (LN_FIRST_POS, fmt.first_digit_position),
            (LN_INT_DIGITS, fmt.integer_digits),
            (LN_DEC_DIGITS, fmt.decimal_digits),
        ):
            if not 0 <= value <= 255:
                raise PrjError("numeric format value does not fit in a byte")
            buf[ln + offset] = value
    object_index = line.object_number if line.object_number is not None else 0
    if not 0 <= object_index <= LN_OBJECT_MASK:
        raise PrjError("object index does not fit in the flags byte")
    flags = object_index << LN_OBJECT_SHIFT
    if line.receive:
        flags |= LN_FLAG_RECEIVE
    if line.send:
        flags |= LN_FLAG_SEND
    if line.alarm:
        flags |= LN_FLAG_ALARM
    buf[ln + LN_FLAGS] = flags
    disp = buf[ln + LN_DISPLAY] & ~(LN_DISP_AUTOSCROLL_OFF | LN_DISP_SHOW_ON_RECEIVE)
    if not line.show_on_autoscroll:
        disp |= LN_DISP_AUTOSCROLL_OFF
    if line.show_on_receive:
        disp |= LN_DISP_SHOW_ON_RECEIVE
    buf[ln + LN_DISPLAY] = disp
    if line.data_type is LineDataType.DATE_EIS4 and line.time_date_format is not None:
        td = buf[ln + LN_TIMEDATE] & ~LN_TD_DATE_MONTH_FIRST
        if line.time_date_format.date_order is DateOrder.MONTH_DAY_YEAR:
            td |= LN_TD_DATE_MONTH_FIRST
        buf[ln + LN_TIMEDATE] = td
    elif line.data_type is LineDataType.TIME_EIS3 and line.time_date_format is not None:
        td = buf[ln + LN_TIMEDATE] & ~(LN_TD_WEEKDAY | LN_TD_WEEKDAY_ENGLISH)
        if line.time_date_format.show_weekday:
            td |= LN_TD_WEEKDAY
        if line.time_date_format.weekday_in_english:
            td |= LN_TD_WEEKDAY_ENGLISH
        buf[ln + LN_TIMEDATE] = td
    if line.data_type is LineDataType.VALUE_1BYTE_EIS6 and line.scaling is not None:
        _patch_eis6_scaling(buf, ln, line.scaling)
    _patch_send_parameters(buf, ln, line)


def _patch_eis6_scaling(buf: bytearray, ln: int, scaling: Eis6Scaling) -> None:
    """Encode the EIS6 display scaling into the line's slope/offset int16s (see module notes)."""
    span = scaling.display_at_255 - scaling.display_at_0
    if span <= 0:
        raise PrjError("EIS6 scaling: display at 0 must be below display at 255")
    coeff = (256 * span + 254) // 255  # ceil(256*span/255)
    _write_u16(buf, ln + LN_COEFF, coeff & 0xFFFF)
    _write_u16(buf, ln + LN_OFFSET, scaling.display_at_0 & 0xFFFF)


def _patch_send_parameters(buf: bytearray, ln: int, line: DisplayLine) -> None:
    """Encode the send-tab bytes a line of its type carries (no-op when none are set)."""
    sp = line.send_parameters
    if sp is None:
        return
    if line.data_type is LineDataType.SWITCH_EIS1 and sp.eis1_mode is not None:
        buf[ln + LN_EIS1_MODE] = _EIS1_MODE_TO_CODE[sp.eis1_mode]
    elif line.data_type is LineDataType.VALUE_2BYTE_EIS5:
        if sp.eis5_min is not None:
            wmin = _eis5_encode(sp.eis5_min)
            buf[ln + LN_EIS5_MIN_LO] = wmin & 0xFF
            buf[ln + LN_EIS5_MIN_HI] = (wmin >> 8) & 0xFF
        if sp.eis5_max is not None:
            wmax = _eis5_encode(sp.eis5_max)
            buf[ln + LN_EIS5_MAX_LO] = wmax & 0xFF
            buf[ln + LN_EIS5_MAX_HI] = (wmax >> 8) & 0xFF
    elif line.data_type is LineDataType.VALUE_1BYTE_EIS6:
        if sp.eis6_min is not None:
            if not 0 <= sp.eis6_min <= 255:
                raise PrjError("EIS6 send minimum must be a byte (0..255)")
            buf[ln + LN_EIS6_SEND_MIN] = sp.eis6_min
        if sp.eis6_max is not None:
            if not 0 <= sp.eis6_max <= 255:
                raise PrjError("EIS6 send maximum must be a byte (0..255)")
            buf[ln + LN_EIS6_SEND_MAX] = sp.eis6_max
