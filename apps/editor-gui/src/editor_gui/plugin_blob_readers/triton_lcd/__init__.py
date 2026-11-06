"""Read + write a Busch-Jaeger triton / 6136UP LCD display configuration as viewer sections.

The display's full configuration (the device options plus every text line and its number / scaling /
time-date / send formatting) lives only in a device ``BinaryData`` entry named ``TritonLcd-Config``,
as the UTF-8 JSON that :mod:`xknxeditor.vendor.triton.serialize` produces - none of it is in the
standard KNX parameter list. The reader surfaces the whole document as one section, grouped into a
"Display" device node plus one node per line; editable fields route back through :func:`apply_edit`,
which patches the single addressed field and re-serialises deterministically.
"""

from __future__ import annotations

from collections.abc import Callable

from editor_gui.plugin_params import PluginParameter, PluginSectionView
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
    alarm_tone_durations,
    alarm_tone_label,
    backlight_modes,
    data_types_for,
    validate_device,
)
from xknxeditor.vendor.triton.serialize import from_json_bytes, to_json_bytes

# The BinaryData entry name this reader owns and routes edits back to.
BLOB_NAME = "TritonLcd-Config"

_REF_PREFIX = "triton_lcd"
_BOOL_CHOICES: tuple[tuple[str, str], ...] = (("True", "True"), ("False", "False"))

_DEVICE_TYPE_LABELS: dict[DeviceType, str] = {
    DeviceType.INTERFACE_6136UP: "6136UP LCD Interface",
    DeviceType.TRITON_6323: "triton 6323",
    DeviceType.TRITON_6325: "triton 6325",
}
_BACKLIGHT_LABELS: dict[BacklightMode, str] = {
    BacklightMode.ALWAYS_OFF: "Always off",
    BacklightMode.ALWAYS_ON: "Always on",
    BacklightMode.ON_RECEIVE: "On receive",
    BacklightMode.OBJECT_CONTROLLED: "Object controlled",
    BacklightMode.ON_ALARM: "On alarm",
}
_DATA_TYPE_LABELS: dict[LineDataType, str] = {
    LineDataType.FREE: "Free (unused)",
    LineDataType.STATIC_TEXT: "Static text",
    LineDataType.SWITCH_EIS1: "Switch (EIS 1)",
    LineDataType.VALUE_1BYTE_EIS6: "1-byte value (EIS 6)",
    LineDataType.VALUE_2BYTE_EIS5: "2-byte value (EIS 5)",
    LineDataType.TIME_EIS3: "Time (EIS 3)",
    LineDataType.DATE_EIS4: "Date (EIS 4)",
}
_DATE_ORDER_LABELS: dict[DateOrder, str] = {
    DateOrder.DAY_MONTH_YEAR: "Day.month.year",
    DateOrder.MONTH_DAY_YEAR: "Month.day.year",
}
_DATE_ORDER_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (o.value, _DATE_ORDER_LABELS[o]) for o in DateOrder
)
# EIS 1 send mode allows "not set" (the line sends nothing yet), so the first option is empty.
_SEND_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    ("", "(Not set)"),
    (SendModeEis1.TOGGLE.value, "Toggle"),
    (SendModeEis1.ON.value, "On"),
    (SendModeEis1.OFF.value, "Off"),
)


def read(data: bytes, name: str = BLOB_NAME) -> list[PluginSectionView]:
    """Surface a triton LCD config as one "LCD Display" section.

    ``data`` is the UTF-8 JSON of the ``TritonLcd-Config`` blob; ``name`` is the BinaryData entry
    name and becomes part of every editable row's ref so :func:`apply_edit` routes back to the right
    blob. Returns ``[]`` when ``data`` is not a triton LCD config.
    """
    if name != BLOB_NAME:
        return []
    try:
        device = from_json_bytes(data)
    except ValueError:
        return []
    if validate_device(device):
        return []
    rows: list[PluginParameter] = list(_device_rows(device, name))
    for line in device.lines:
        rows.extend(_line_rows(device, line, name))
    return [PluginSectionView(section="LCD Display", parameters=rows)]


def _row(
    ref: str,
    label: str,
    value: str,
    *,
    writable: bool = False,
    choices: tuple[tuple[str, str], ...] = (),
    group: str = "",
    group_key: str = "",
) -> PluginParameter:
    return PluginParameter(
        ref_id=ref,
        name=ref,
        label=label,
        value=value,
        default_value="",
        section="",
        display_order=0,
        writable=writable,
        choices=choices,
        group=group,
        group_key=group_key,
    )


def _bool_row(
    ref: str, label: str, value: bool, group: str, group_key: str
) -> PluginParameter:
    return _row(
        ref,
        label,
        str(value),
        writable=True,
        choices=_BOOL_CHOICES,
        group=group,
        group_key=group_key,
    )


def _backlight_choices(device_type: DeviceType) -> tuple[tuple[str, str], ...]:
    return tuple(
        (m.value, _BACKLIGHT_LABELS.get(m, m.value))
        for m in backlight_modes(device_type)
    )


def _data_type_choices(
    device_type: DeviceType, line_index: int
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (t.value, _DATA_TYPE_LABELS.get(t, t.value))
        for t in data_types_for(device_type, line_index)
    )


def _alarm_tone_choices(device_type: DeviceType) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(s), alarm_tone_label(s)) for s in alarm_tone_durations(device_type)
    )


_ALARM_SCROLL_CHOICES: tuple[tuple[str, str], ...] = (
    ("False", "After 1 s"),
    ("True", "After 3 s"),
)


def _device_rows(device: DisplayDevice, name: str) -> list[PluginParameter]:
    group, gkey = "Display", "device"
    dt = device.device_type
    base = f"{_REF_PREFIX}:{name}:device"
    rows = [
        _row(
            f"{base}:name",
            "Name",
            device.name,
            writable=True,
            group=group,
            group_key=gkey,
        ),
        _row(
            f"{base}:physical_address",
            "Physical Address",
            device.physical_address,
            writable=True,
            group=group,
            group_key=gkey,
        ),
        _row(
            f"{base}:backlight",
            "Backlight",
            device.backlight.value,
            writable=True,
            choices=_backlight_choices(dt),
            group=group,
            group_key=gkey,
        ),
        _row(
            f"{base}:alarm_tone_duration_s",
            "Alarm Tone Duration",
            str(device.alarm_tone_duration_s),
            writable=True,
            choices=_alarm_tone_choices(dt),
            group=group,
            group_key=gkey,
        ),
    ]
    if not dt.is_triton:
        # The keypress tone is a 6136UP-only control (hidden for a TRITON).
        rows.append(
            _bool_row(
                f"{base}:beep_on_keypress",
                "Beep on Keypress",
                device.beep_on_keypress,
                group,
                gkey,
            )
        )
    if dt.is_triton:
        # Scroll-with-key and the alarm scroll/blink are TRITON-only.
        rows.append(
            _bool_row(
                f"{base}:scroll_with_key",
                "Scroll With Key",
                device.scroll_with_key,
                group,
                gkey,
            )
        )
        rows.append(
            _row(
                f"{base}:alarm_scroll_after_3s",
                "Alarm Scroll Interval",
                str(device.alarm_scroll_after_3s),
                writable=True,
                choices=_ALARM_SCROLL_CHOICES,
                group=group,
                group_key=gkey,
            )
        )
        rows.append(
            _bool_row(
                f"{base}:key_backlight_blinks_on_alarm",
                "Key Backlight Blinks on Alarm",
                device.key_backlight_blinks_on_alarm,
                group,
                gkey,
            )
        )
    # Device type comes from the product, not the config; read-only, so its ref never routes an edit.
    rows.append(
        _row(
            "display:device-type",
            "Device Type",
            _DEVICE_TYPE_LABELS.get(dt, dt.value),
            group=group,
            group_key=gkey,
        )
    )
    return rows


def _line_rows(
    device: DisplayDevice, line: DisplayLine, name: str
) -> list[PluginParameter]:
    dt = device.device_type
    idx = line.index
    head = f"[{idx}] Line"
    if line.text_off:
        head = f"{head} {line.text_off!r}"
    gkey = f"line-{idx}"
    base = f"{_REF_PREFIX}:{name}:line:{idx}"
    ldt = line.data_type
    rows = [
        _row(
            f"{base}:data_type",
            "Data Type",
            ldt.value,
            writable=True,
            choices=_data_type_choices(dt, idx),
            group=head,
            group_key=gkey,
        )
    ]
    if ldt is LineDataType.FREE:
        return rows
    rows.append(
        _row(
            f"{base}:text_off",
            "Text at Off",
            line.text_off,
            writable=True,
            group=head,
            group_key=gkey,
        )
    )
    if ldt is LineDataType.SWITCH_EIS1:
        rows.append(
            _row(
                f"{base}:text_on",
                "Text at On",
                line.text_on,
                writable=True,
                group=head,
                group_key=gkey,
            )
        )
    if ldt.has_object:
        rows.append(
            _row(
                f"{base}:object_number",
                "Object Number",
                "" if line.object_number is None else str(line.object_number),
                writable=True,
                group=head,
                group_key=gkey,
            )
        )
        rows.append(_bool_row(f"{base}:receive", "Receive", line.receive, head, gkey))
        if dt.has_send_function:
            rows.append(_bool_row(f"{base}:send", "Send", line.send, head, gkey))
        if ldt is LineDataType.SWITCH_EIS1:
            rows.append(_bool_row(f"{base}:alarm", "Alarm", line.alarm, head, gkey))
        rows.append(
            _bool_row(
                f"{base}:show_on_autoscroll",
                "Show on Autoscroll",
                line.show_on_autoscroll,
                head,
                gkey,
            )
        )
        rows.append(
            _bool_row(
                f"{base}:show_on_receive",
                "Show on Receive",
                line.show_on_receive,
                head,
                gkey,
            )
        )
        if ldt is LineDataType.SWITCH_EIS1:
            rows.append(
                _bool_row(
                    f"{base}:beep_on_receive",
                    "Beep on Receive",
                    line.beep_on_receive,
                    head,
                    gkey,
                )
            )
    if ldt.is_number:
        rows.extend(_number_format_rows(line, base, head, gkey))
    if ldt is LineDataType.VALUE_1BYTE_EIS6:
        rows.extend(_scaling_rows(line, base, head, gkey))
    if ldt.is_time_or_date:
        rows.extend(_time_date_rows(line, base, head, gkey))
    if line.send and dt.has_send_function:
        rows.extend(_send_rows(line, base, head, gkey))
    return rows


def _number_format_rows(
    line: DisplayLine, base: str, head: str, gkey: str
) -> list[PluginParameter]:
    nf = line.number_format or NumberFormat()
    b = f"{base}:format"
    return [
        _row(
            f"{b}:first_digit_position",
            "First Digit Position",
            str(nf.first_digit_position),
            writable=True,
            group=head,
            group_key=gkey,
        ),
        _row(
            f"{b}:integer_digits",
            "Integer Digits",
            str(nf.integer_digits),
            writable=True,
            group=head,
            group_key=gkey,
        ),
        _row(
            f"{b}:decimal_digits",
            "Decimal Digits",
            str(nf.decimal_digits),
            writable=True,
            group=head,
            group_key=gkey,
        ),
        _bool_row(
            f"{b}:show_leading_zeros",
            "Show Leading Zeros",
            nf.show_leading_zeros,
            head,
            gkey,
        ),
        _bool_row(
            f"{b}:extra_sign_position",
            "Extra Sign Position",
            nf.extra_sign_position,
            head,
            gkey,
        ),
        _bool_row(
            f"{b}:show_positive_sign",
            "Show Positive Sign",
            nf.show_positive_sign,
            head,
            gkey,
        ),
        _bool_row(
            f"{b}:round_last_digit_to_0_or_5",
            "Round Last Digit to 0 or 5",
            nf.round_last_digit_to_0_or_5,
            head,
            gkey,
        ),
    ]


def _scaling_rows(
    line: DisplayLine, base: str, head: str, gkey: str
) -> list[PluginParameter]:
    sc = line.scaling or Eis6Scaling()
    b = f"{base}:scaling"
    return [
        _row(
            f"{b}:display_at_0",
            "Display at 0",
            str(sc.display_at_0),
            writable=True,
            group=head,
            group_key=gkey,
        ),
        _row(
            f"{b}:display_at_255",
            "Display at 255",
            str(sc.display_at_255),
            writable=True,
            group=head,
            group_key=gkey,
        ),
    ]


def _time_date_rows(
    line: DisplayLine, base: str, head: str, gkey: str
) -> list[PluginParameter]:
    td = line.time_date_format or TimeDateFormat()
    b = f"{base}:timedate"
    if line.data_type is LineDataType.DATE_EIS4:
        return [
            _row(
                f"{b}:date_order",
                "Date Order",
                td.date_order.value,
                writable=True,
                choices=_DATE_ORDER_CHOICES,
                group=head,
                group_key=gkey,
            ),
        ]
    # A time line shows the weekday options instead of a date order.
    return [
        _bool_row(f"{b}:show_weekday", "Show Weekday", td.show_weekday, head, gkey),
        _bool_row(
            f"{b}:weekday_in_english",
            "Weekday in English",
            td.weekday_in_english,
            head,
            gkey,
        ),
    ]


def _send_rows(
    line: DisplayLine, base: str, head: str, gkey: str
) -> list[PluginParameter]:
    sp = line.send_parameters or SendParameters()
    b = f"{base}:send"
    return [
        _row(
            f"{b}:eis1_mode",
            "Send Mode (EIS 1)",
            "" if sp.eis1_mode is None else sp.eis1_mode.value,
            writable=True,
            choices=_SEND_MODE_CHOICES,
            group=head,
            group_key=gkey,
        ),
        _row(
            f"{b}:eis5_min",
            "Send Minimum (EIS 5)",
            "" if sp.eis5_min is None else str(sp.eis5_min),
            writable=True,
            group=head,
            group_key=gkey,
        ),
        _row(
            f"{b}:eis5_max",
            "Send Maximum (EIS 5)",
            "" if sp.eis5_max is None else str(sp.eis5_max),
            writable=True,
            group=head,
            group_key=gkey,
        ),
        _row(
            f"{b}:eis6_min",
            "Send Minimum (EIS 6)",
            "" if sp.eis6_min is None else str(sp.eis6_min),
            writable=True,
            group=head,
            group_key=gkey,
        ),
        _row(
            f"{b}:eis6_max",
            "Send Maximum (EIS 6)",
            "" if sp.eis6_max is None else str(sp.eis6_max),
            writable=True,
            group=head,
            group_key=gkey,
        ),
    ]


# --- edit routing -----------------------------------------------------------------------------


def _as_bool(value: str) -> bool:
    if value not in ("True", "False"):
        raise ValueError(f"expected 'True' or 'False', got {value!r}")
    return value == "True"


def _as_int_or_none(value: str) -> int | None:
    return int(value) if value else None


def _as_float_or_none(value: str) -> float | None:
    return float(value) if value else None


_DEVICE_CONV: dict[str, Callable[[str], object]] = {
    "name": str,
    "physical_address": str,
    "backlight": lambda v: BacklightMode(v),
    "beep_on_keypress": _as_bool,
    "scroll_with_key": _as_bool,
    "key_backlight_blinks_on_alarm": _as_bool,
    "alarm_tone_duration_s": int,
    "alarm_scroll_after_3s": _as_bool,
}
_LINE_CONV: dict[str, Callable[[str], object]] = {
    "data_type": lambda v: LineDataType(v),
    "text_off": str,
    "text_on": str,
    "object_number": _as_int_or_none,
    "send": _as_bool,
    "receive": _as_bool,
    "alarm": _as_bool,
    "show_on_autoscroll": _as_bool,
    "show_on_receive": _as_bool,
    "beep_on_receive": _as_bool,
}
_FORMAT_CONV: dict[str, Callable[[str], object]] = {
    "first_digit_position": int,
    "integer_digits": int,
    "decimal_digits": int,
    "show_leading_zeros": _as_bool,
    "extra_sign_position": _as_bool,
    "show_positive_sign": _as_bool,
    "round_last_digit_to_0_or_5": _as_bool,
}
_SCALING_CONV: dict[str, Callable[[str], object]] = {
    "display_at_0": int,
    "display_at_255": int,
}
_TIMEDATE_CONV: dict[str, Callable[[str], object]] = {
    "show_weekday": _as_bool,
    "weekday_in_english": _as_bool,
    "date_order": lambda v: DateOrder(v),
}
_SEND_CONV: dict[str, Callable[[str], object]] = {
    "eis1_mode": lambda v: SendModeEis1(v) if v else None,
    "eis5_min": _as_float_or_none,
    "eis5_max": _as_float_or_none,
    "eis6_min": _as_int_or_none,
    "eis6_max": _as_int_or_none,
}
# sub-block token -> (DisplayLine field, factory for a missing block, field converters)
_SUBS: dict[
    str, tuple[str, Callable[[], object], dict[str, Callable[[str], object]]]
] = {
    "format": ("number_format", NumberFormat, _FORMAT_CONV),
    "scaling": ("scaling", Eis6Scaling, _SCALING_CONV),
    "timedate": ("time_date_format", TimeDateFormat, _TIMEDATE_CONV),
    "send": ("send_parameters", SendParameters, _SEND_CONV),
}


def apply_edit(data: bytes, ref_id: str, new_value: str) -> bytes:
    """Patch the single field addressed by ``ref_id`` and return the new JSON bytes.

    ``ref_id`` is ``triton_lcd:<name>:device:<attr>``, ``triton_lcd:<name>:line:<index>:<attr>`` or
    ``triton_lcd:<name>:line:<index>:<sub>:<attr>`` (``sub`` in ``format`` / ``scaling`` /
    ``timedate`` / ``send``). A missing sub-block is created on demand. A no-op edit returns the
    input bytes verbatim (the serialisation is deterministic). Raises ``ValueError`` for a foreign
    or unknown ref, or a value that does not fit the field's type.
    """
    parts = ref_id.split(":")
    if len(parts) < 4 or parts[0] != _REF_PREFIX or parts[1] != BLOB_NAME:
        raise ValueError(f"not a triton_lcd ref: {ref_id!r}")
    try:
        device = from_json_bytes(data)
    except ValueError as exc:
        raise ValueError("payload is not a triton LCD config") from exc
    before = to_json_bytes(device)
    kind = parts[2]
    if kind == "device" and len(parts) == 4:
        _set_attr(device, _DEVICE_CONV, parts[3], new_value, ref_id)
    elif kind == "line" and len(parts) == 5:
        _set_attr(
            _find_line(device, parts[3], ref_id),
            _LINE_CONV,
            parts[4],
            new_value,
            ref_id,
        )
    elif kind == "line" and len(parts) == 6:
        _set_sub(
            _find_line(device, parts[3], ref_id), parts[4], parts[5], new_value, ref_id
        )
    else:
        raise ValueError(f"unrecognised triton_lcd ref: {ref_id!r}")
    issues = validate_device(device)
    if issues:
        raise ValueError(f"edit would make the config invalid: {issues[0]}")
    result = to_json_bytes(device)
    return data if result == before else result


def _find_line(device: DisplayDevice, index_str: str, ref_id: str) -> DisplayLine:
    try:
        index = int(index_str)
    except ValueError as exc:
        raise ValueError(f"bad line index: {ref_id!r}") from exc
    for line in device.lines:
        if line.index == index:
            return line
    raise ValueError(f"no such line: {ref_id!r}")


def _set_attr(
    target: object,
    conv: dict[str, Callable[[str], object]],
    attr: str,
    new_value: str,
    ref_id: str,
) -> None:
    converter = conv.get(attr)
    if converter is None:
        raise ValueError(f"unknown attribute: {ref_id!r}")
    setattr(target, attr, converter(new_value))


def _set_sub(
    line: DisplayLine, sub: str, attr: str, new_value: str, ref_id: str
) -> None:
    spec = _SUBS.get(sub)
    if spec is None:
        raise ValueError(f"unknown sub-block: {ref_id!r}")
    field_name, factory, conv = spec
    block = getattr(line, field_name)
    if block is None:
        block = factory()
        _set_attr(block, conv, attr, new_value, ref_id)
        if block != factory():
            setattr(line, field_name, block)
    else:
        _set_attr(block, conv, attr, new_value, ref_id)


__all__ = ["BLOB_NAME", "apply_edit", "read"]
