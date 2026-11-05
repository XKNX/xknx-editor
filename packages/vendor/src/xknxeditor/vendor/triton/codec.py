"""Encode/decode a triton display configuration to the device text-memory blob.

EXPERIMENTAL / UNVERIFIED. The real Busch-Jaeger LVS writes the display configuration into the
display's own memory through the Falcon COM stack, and the manual states the text is stored
*compressed* — so there is no confirmed fixed byte layout, and no hardware/pcap was available to
validate one. The layout below is a self-consistent RECONSTRUCTION derived from the recovered field
model: it round-trips (``decode_display(encode_display(d)) == d``) and gives a concrete payload to
try against a device, but it is almost certainly NOT byte-compatible with the original LVS until a
capture pins the real format down. Keep all layout constants in this one module so a capture can
correct them without touching the model or the UI.

The header carries a private magic so a blob written by this editor can be told apart from anything
else; it is not the device's own marker.
"""

from __future__ import annotations

import struct

from xknxeditor.vendor.triton.model import (
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

# Marks this as a reconstruction, not the device's own format.
EXPERIMENTAL = True

_MAGIC = b"XTL"  # xknx triton LCD (private marker)
_VERSION = 1
_NO_OBJECT = 0xFFFF

# Stable on-disk ordinals, independent of the Enum definition order, so reordering the model never
# silently changes the encoding.
_DEVICE_TYPE_CODES: dict[DeviceType, int] = {
    DeviceType.INTERFACE_6136UP: 0,
    DeviceType.TRITON_6323: 1,
    DeviceType.TRITON_6325: 2,
}
_DATA_TYPE_CODES: dict[LineDataType, int] = {
    LineDataType.FREE: 0,
    LineDataType.STATIC_TEXT: 1,
    LineDataType.SWITCH_EIS1: 2,
    LineDataType.VALUE_1BYTE_EIS6: 3,
    LineDataType.VALUE_2BYTE_EIS5: 4,
    LineDataType.TIME_EIS3: 5,
    LineDataType.DATE_EIS4: 6,
}
_BACKLIGHT_CODES: dict[BacklightMode, int] = {
    BacklightMode.ALWAYS_OFF: 0,
    BacklightMode.ALWAYS_ON: 1,
    BacklightMode.ON_RECEIVE: 2,
    BacklightMode.OBJECT_CONTROLLED: 3,
    BacklightMode.ON_ALARM: 4,
    BacklightMode.LIKE_TEXT_FIELDS: 5,
}
_SEND_MODE_CODES: dict[SendModeEis1, int] = {
    SendModeEis1.TOGGLE: 0,
    SendModeEis1.ON: 1,
    SendModeEis1.OFF: 2,
}


class TritonCodecError(ValueError):
    """Raised when a blob cannot be decoded as an editor-written triton config."""


_DEVICE_TYPE_BY_CODE = {v: k for k, v in _DEVICE_TYPE_CODES.items()}
_DATA_TYPE_BY_CODE = {v: k for k, v in _DATA_TYPE_CODES.items()}
_BACKLIGHT_BY_CODE = {v: k for k, v in _BACKLIGHT_CODES.items()}
_SEND_MODE_BY_CODE = {v: k for k, v in _SEND_MODE_CODES.items()}


# --- small writers/readers -------------------------------------------------


class _Writer:
    def __init__(self) -> None:
        self._out = bytearray()

    def u8(self, v: int) -> None:
        self._out.append(v & 0xFF)

    def raw(self, data: bytes) -> None:
        self._out += data

    def u16(self, v: int) -> None:
        self._out += struct.pack("<H", v & 0xFFFF)

    def i16(self, v: int) -> None:
        self._out += struct.pack("<h", v)

    def f32(self, v: float) -> None:
        self._out += struct.pack("<f", v)

    def text(self, s: str) -> None:
        raw = s.encode("latin-1", "replace")[:255]
        self._out.append(len(raw))
        self._out += raw

    def bytes(self) -> bytes:
        return bytes(self._out)


class _Reader:
    def __init__(self, data: bytes) -> None:
        self._d = data
        self._i = 0

    def _need(self, n: int) -> None:
        if self._i + n > len(self._d):
            raise TritonCodecError("blob truncated")

    def u8(self) -> int:
        self._need(1)
        v = self._d[self._i]
        self._i += 1
        return v

    def skip(self, n: int) -> None:
        self._need(n)
        self._i += n

    def u16(self) -> int:
        self._need(2)
        v = struct.unpack_from("<H", self._d, self._i)[0]
        self._i += 2
        return v

    def i16(self) -> int:
        self._need(2)
        v = struct.unpack_from("<h", self._d, self._i)[0]
        self._i += 2
        return v

    def f32(self) -> float:
        self._need(4)
        v = struct.unpack_from("<f", self._d, self._i)[0]
        self._i += 4
        return float(v)

    def text(self) -> str:
        n = self.u8()
        self._need(n)
        s = self._d[self._i : self._i + n].decode("latin-1", "replace")
        self._i += n
        return s

    def at_end(self) -> bool:
        return self._i >= len(self._d)


# --- per-line flag bits ----------------------------------------------------

_F_RECEIVE = 1 << 0
_F_SEND = 1 << 1
_F_ALARM = 1 << 2
_F_AUTOSCROLL = 1 << 3
_F_SHOW_ON_RECEIVE = 1 << 4
_F_BEEP = 1 << 5

_NF_LEADING_ZEROS = 1 << 0
_NF_EXTRA_SIGN = 1 << 1
_NF_POSITIVE_SIGN = 1 << 2
_NF_ROUND_0_5 = 1 << 3

_B_BEEP_KEY = 1 << 0
_B_SCROLL_KEY = 1 << 1
_B_KEY_BLINK = 1 << 2

# which optional sub-blocks follow a line
_SB_NUMBER = 1 << 0
_SB_SCALING = 1 << 1
_SB_TIMEDATE = 1 << 2
_SB_SEND = 1 << 3

_TD_WEEKDAY = 1 << 0
_TD_ENGLISH = 1 << 1
_TD_MONTH_FIRST = 1 << 2

_SP_EIS1 = 1 << 0
_SP_EIS5 = 1 << 1
_SP_EIS6 = 1 << 2


def encode_display(device: DisplayDevice) -> bytes:
    """Serialise ``device`` to the (reconstructed) display blob. See the module note."""
    w = _Writer()
    w.raw(_MAGIC)
    w.u8(_VERSION)
    w.u8(_DEVICE_TYPE_CODES[device.device_type])
    bflags = 0
    if device.beep_on_keypress:
        bflags |= _B_BEEP_KEY
    if device.scroll_with_key:
        bflags |= _B_SCROLL_KEY
    if device.key_backlight_blinks_on_alarm:
        bflags |= _B_KEY_BLINK
    w.u8(bflags)
    w.u8(_BACKLIGHT_CODES[device.backlight])
    w.text(device.name)
    w.text(device.physical_address)
    w.u8(len(device.lines))
    for line in device.lines:
        _encode_line(w, line)
    return w.bytes()


def _encode_line(w: _Writer, line: DisplayLine) -> None:
    w.u8(line.index)
    w.u8(_DATA_TYPE_CODES[line.data_type])
    flags = 0
    if line.receive:
        flags |= _F_RECEIVE
    if line.send:
        flags |= _F_SEND
    if line.alarm:
        flags |= _F_ALARM
    if line.show_on_autoscroll:
        flags |= _F_AUTOSCROLL
    if line.show_on_receive:
        flags |= _F_SHOW_ON_RECEIVE
    if line.beep_on_receive:
        flags |= _F_BEEP
    w.u8(flags)
    w.u16(_NO_OBJECT if line.object_number is None else line.object_number)
    w.text(line.text_off)
    w.text(line.text_on)
    present = 0
    if line.number_format is not None:
        present |= _SB_NUMBER
    if line.scaling is not None:
        present |= _SB_SCALING
    if line.time_date_format is not None:
        present |= _SB_TIMEDATE
    if line.send_parameters is not None:
        present |= _SB_SEND
    w.u8(present)
    if line.number_format is not None:
        _encode_number_format(w, line.number_format)
    if line.scaling is not None:
        w.i16(line.scaling.display_at_0)
        w.i16(line.scaling.display_at_255)
    if line.time_date_format is not None:
        _encode_time_date(w, line.time_date_format)
    if line.send_parameters is not None:
        _encode_send(w, line.send_parameters)


def _encode_number_format(w: _Writer, fmt: NumberFormat) -> None:
    w.u8(fmt.first_digit_position)
    w.u8(fmt.integer_digits)
    w.u8(fmt.decimal_digits)
    nf = 0
    if fmt.show_leading_zeros:
        nf |= _NF_LEADING_ZEROS
    if fmt.extra_sign_position:
        nf |= _NF_EXTRA_SIGN
    if fmt.show_positive_sign:
        nf |= _NF_POSITIVE_SIGN
    if fmt.round_last_digit_to_0_or_5:
        nf |= _NF_ROUND_0_5
    w.u8(nf)


def _encode_time_date(w: _Writer, fmt: TimeDateFormat) -> None:
    td = 0
    if fmt.show_weekday:
        td |= _TD_WEEKDAY
    if fmt.weekday_in_english:
        td |= _TD_ENGLISH
    if fmt.date_order is DateOrder.MONTH_DAY_YEAR:
        td |= _TD_MONTH_FIRST
    w.u8(td)


def _encode_send(w: _Writer, sp: SendParameters) -> None:
    which = 0
    if sp.eis1_mode is not None:
        which |= _SP_EIS1
    if sp.eis5_min is not None and sp.eis5_max is not None:
        which |= _SP_EIS5
    if sp.eis6_min is not None and sp.eis6_max is not None:
        which |= _SP_EIS6
    w.u8(which)
    if which & _SP_EIS1:
        assert sp.eis1_mode is not None
        w.u8(_SEND_MODE_CODES[sp.eis1_mode])
    if which & _SP_EIS5:
        assert sp.eis5_min is not None and sp.eis5_max is not None
        w.f32(sp.eis5_min)
        w.f32(sp.eis5_max)
    if which & _SP_EIS6:
        assert sp.eis6_min is not None and sp.eis6_max is not None
        w.i16(sp.eis6_min)
        w.i16(sp.eis6_max)


def decode_display(data: bytes) -> DisplayDevice:
    """Parse a blob written by :func:`encode_display` back into a :class:`DisplayDevice`."""
    r = _Reader(data)
    if data[:3] != _MAGIC:
        raise TritonCodecError("not an editor-written triton display blob")
    r.skip(3)
    version = r.u8()
    if version != _VERSION:
        raise TritonCodecError(f"unsupported blob version {version}")
    device_type = _DEVICE_TYPE_BY_CODE.get(r.u8())
    if device_type is None:
        raise TritonCodecError("unknown device type")
    bflags = r.u8()
    backlight = _BACKLIGHT_BY_CODE.get(r.u8())
    if backlight is None:
        raise TritonCodecError("unknown backlight mode")
    name = r.text()
    address = r.text()
    count = r.u8()
    lines = [_decode_line(r) for _ in range(count)]
    return DisplayDevice(
        device_type=device_type,
        name=name,
        physical_address=address,
        backlight=backlight,
        beep_on_keypress=bool(bflags & _B_BEEP_KEY),
        scroll_with_key=bool(bflags & _B_SCROLL_KEY),
        key_backlight_blinks_on_alarm=bool(bflags & _B_KEY_BLINK),
        lines=lines,
    )


def _decode_line(r: _Reader) -> DisplayLine:
    index = r.u8()
    data_type = _DATA_TYPE_BY_CODE.get(r.u8())
    if data_type is None:
        raise TritonCodecError("unknown line data type")
    flags = r.u8()
    obj = r.u16()
    text_off = r.text()
    text_on = r.text()
    present = r.u8()
    number_format = _decode_number_format(r) if present & _SB_NUMBER else None
    scaling = (
        Eis6Scaling(display_at_0=r.i16(), display_at_255=r.i16())
        if present & _SB_SCALING
        else None
    )
    time_date = _decode_time_date(r) if present & _SB_TIMEDATE else None
    send_params = _decode_send(r) if present & _SB_SEND else None
    return DisplayLine(
        index=index,
        data_type=data_type,
        text_off=text_off,
        text_on=text_on,
        object_number=None if obj == _NO_OBJECT else obj,
        send=bool(flags & _F_SEND),
        receive=bool(flags & _F_RECEIVE),
        alarm=bool(flags & _F_ALARM),
        show_on_autoscroll=bool(flags & _F_AUTOSCROLL),
        show_on_receive=bool(flags & _F_SHOW_ON_RECEIVE),
        beep_on_receive=bool(flags & _F_BEEP),
        number_format=number_format,
        scaling=scaling,
        time_date_format=time_date,
        send_parameters=send_params,
    )


def _decode_number_format(r: _Reader) -> NumberFormat:
    first = r.u8()
    integer = r.u8()
    decimal = r.u8()
    nf = r.u8()
    return NumberFormat(
        first_digit_position=first,
        integer_digits=integer,
        decimal_digits=decimal,
        show_leading_zeros=bool(nf & _NF_LEADING_ZEROS),
        extra_sign_position=bool(nf & _NF_EXTRA_SIGN),
        show_positive_sign=bool(nf & _NF_POSITIVE_SIGN),
        round_last_digit_to_0_or_5=bool(nf & _NF_ROUND_0_5),
    )


def _decode_time_date(r: _Reader) -> TimeDateFormat:
    td = r.u8()
    return TimeDateFormat(
        show_weekday=bool(td & _TD_WEEKDAY),
        weekday_in_english=bool(td & _TD_ENGLISH),
        date_order=(
            DateOrder.MONTH_DAY_YEAR
            if td & _TD_MONTH_FIRST
            else DateOrder.DAY_MONTH_YEAR
        ),
    )


def _decode_send(r: _Reader) -> SendParameters:
    which = r.u8()
    eis1 = _SEND_MODE_BY_CODE.get(r.u8()) if which & _SP_EIS1 else None
    eis5_min = r.f32() if which & _SP_EIS5 else None
    eis5_max = r.f32() if which & _SP_EIS5 else None
    eis6_min = r.i16() if which & _SP_EIS6 else None
    eis6_max = r.i16() if which & _SP_EIS6 else None
    return SendParameters(
        eis1_mode=eis1,
        eis5_min=eis5_min,
        eis5_max=eis5_max,
        eis6_min=eis6_min,
        eis6_max=eis6_max,
    )
