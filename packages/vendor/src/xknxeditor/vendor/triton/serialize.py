"""JSON serialisation of a :class:`DisplayDevice` for storing it in the project.

The project keeps a device's LCD configuration as a small JSON document (inside a device
``BinaryData`` entry). This is the stable, editable representation — distinct from the experimental
device ``codec`` bytes, which are only produced when programming the physical device. Enums are
stored by their ``.value`` so the document stays human-readable and stable across refactors.
"""

from __future__ import annotations

import json
import math
from typing import Any

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

SCHEMA_VERSION = 1


def device_to_dict(device: DisplayDevice) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "device_type": device.device_type.value,
        "name": device.name,
        "physical_address": device.physical_address,
        "backlight": device.backlight.value,
        "beep_on_keypress": device.beep_on_keypress,
        "scroll_with_key": device.scroll_with_key,
        "key_backlight_blinks_on_alarm": device.key_backlight_blinks_on_alarm,
        "alarm_tone_duration_s": device.alarm_tone_duration_s,
        "alarm_scroll_after_3s": device.alarm_scroll_after_3s,
        "lines": [_line_to_dict(line) for line in device.lines],
    }


def _line_to_dict(line: DisplayLine) -> dict[str, Any]:
    out: dict[str, Any] = {
        "index": line.index,
        "data_type": line.data_type.value,
        "text_off": line.text_off,
        "text_on": line.text_on,
        "object_number": line.object_number,
        "send": line.send,
        "receive": line.receive,
        "alarm": line.alarm,
        "show_on_autoscroll": line.show_on_autoscroll,
        "show_on_receive": line.show_on_receive,
        "beep_on_receive": line.beep_on_receive,
    }
    if line.number_format is not None:
        nf = line.number_format
        out["number_format"] = {
            "first_digit_position": nf.first_digit_position,
            "integer_digits": nf.integer_digits,
            "decimal_digits": nf.decimal_digits,
            "show_leading_zeros": nf.show_leading_zeros,
            "extra_sign_position": nf.extra_sign_position,
            "show_positive_sign": nf.show_positive_sign,
            "round_last_digit_to_0_or_5": nf.round_last_digit_to_0_or_5,
        }
    if line.scaling is not None:
        out["scaling"] = {
            "display_at_0": line.scaling.display_at_0,
            "display_at_255": line.scaling.display_at_255,
        }
    if line.time_date_format is not None:
        td = line.time_date_format
        out["time_date_format"] = {
            "show_weekday": td.show_weekday,
            "weekday_in_english": td.weekday_in_english,
            "date_order": td.date_order.value,
        }
    if line.send_parameters is not None:
        sp = line.send_parameters
        out["send_parameters"] = {
            "eis1_mode": sp.eis1_mode.value if sp.eis1_mode is not None else None,
            "eis5_min": sp.eis5_min,
            "eis5_max": sp.eis5_max,
            "eis6_min": sp.eis6_min,
            "eis6_max": sp.eis6_max,
        }
    return out


def _boolean(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"expected a JSON boolean, got {value!r}")
    return value


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"expected a JSON integer, got {value!r}")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"expected a JSON string, got {value!r}")
    return value


def _optional_integer(value: object) -> int | None:
    return None if value is None else _integer(value)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"expected a finite JSON number, got {value!r}")
    return float(value)


def device_from_dict(data: dict[str, Any]) -> DisplayDevice:
    if _integer(data.get("schema", SCHEMA_VERSION)) != SCHEMA_VERSION:
        raise ValueError("unsupported triton LCD config schema")
    if not isinstance(data.get("lines", []), list):
        raise ValueError("expected a JSON array of lines")
    return DisplayDevice(
        device_type=DeviceType(data["device_type"]),
        name=_text(data.get("name", "")),
        physical_address=_text(data.get("physical_address", "")),
        backlight=BacklightMode(data.get("backlight", BacklightMode.ALWAYS_ON.value)),
        beep_on_keypress=_boolean(data.get("beep_on_keypress", False)),
        scroll_with_key=_boolean(data.get("scroll_with_key", True)),
        key_backlight_blinks_on_alarm=_boolean(
            data.get("key_backlight_blinks_on_alarm", False)
        ),
        alarm_tone_duration_s=_integer(data.get("alarm_tone_duration_s", 0)),
        alarm_scroll_after_3s=_boolean(data.get("alarm_scroll_after_3s", False)),
        lines=[_line_from_dict(item) for item in data.get("lines", [])],
    )


def _line_from_dict(data: dict[str, Any]) -> DisplayLine:
    nf = data.get("number_format")
    scaling = data.get("scaling")
    td = data.get("time_date_format")
    sp = data.get("send_parameters")
    return DisplayLine(
        index=_integer(data["index"]),
        data_type=LineDataType(data.get("data_type", LineDataType.FREE.value)),
        text_off=_text(data.get("text_off", "")),
        text_on=_text(data.get("text_on", "")),
        object_number=_optional_integer(data.get("object_number")),
        send=_boolean(data.get("send", False)),
        receive=_boolean(data.get("receive", True)),
        alarm=_boolean(data.get("alarm", False)),
        show_on_autoscroll=_boolean(data.get("show_on_autoscroll", True)),
        show_on_receive=_boolean(data.get("show_on_receive", False)),
        beep_on_receive=_boolean(data.get("beep_on_receive", False)),
        number_format=(
            NumberFormat(
                first_digit_position=_integer(nf["first_digit_position"]),
                integer_digits=_integer(nf["integer_digits"]),
                decimal_digits=_integer(nf["decimal_digits"]),
                show_leading_zeros=_boolean(nf["show_leading_zeros"]),
                extra_sign_position=_boolean(nf["extra_sign_position"]),
                show_positive_sign=_boolean(nf["show_positive_sign"]),
                round_last_digit_to_0_or_5=_boolean(nf["round_last_digit_to_0_or_5"]),
            )
            if nf is not None
            else None
        ),
        scaling=(
            Eis6Scaling(
                display_at_0=_integer(scaling["display_at_0"]),
                display_at_255=_integer(scaling["display_at_255"]),
            )
            if scaling is not None
            else None
        ),
        time_date_format=(
            TimeDateFormat(
                show_weekday=_boolean(td["show_weekday"]),
                weekday_in_english=_boolean(td["weekday_in_english"]),
                date_order=DateOrder(td["date_order"]),
            )
            if td is not None
            else None
        ),
        send_parameters=(
            SendParameters(
                eis1_mode=(
                    SendModeEis1(sp["eis1_mode"])
                    if sp.get("eis1_mode") is not None
                    else None
                ),
                eis5_min=_optional_float(sp.get("eis5_min")),
                eis5_max=_optional_float(sp.get("eis5_max")),
                eis6_min=_optional_integer(sp.get("eis6_min")),
                eis6_max=_optional_integer(sp.get("eis6_max")),
            )
            if sp is not None
            else None
        ),
    )


def to_json_bytes(device: DisplayDevice) -> bytes:
    """Serialise ``device`` to the UTF-8 JSON bytes stored in the project blob."""
    return json.dumps(
        device_to_dict(device), ensure_ascii=False, indent=0, allow_nan=False
    ).encode("utf-8")


def from_json_bytes(data: bytes) -> DisplayDevice:
    """Parse the project-blob JSON bytes back into a :class:`DisplayDevice`."""
    try:
        return device_from_dict(json.loads(data.decode("utf-8")))
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError("payload is not a triton LCD config") from exc


def default_device(
    device_type: DeviceType, name: str, physical_address: str
) -> DisplayDevice:
    """A fresh configuration: every line unused, sensible device defaults for the type."""
    backlight = (
        BacklightMode.ALWAYS_ON if device_type.is_triton else BacklightMode.ON_RECEIVE
    )
    # The alarm signal-tone default is the first duration the family offers (TRITON 1 s, 6136UP 10 s).
    tone = 1 if device_type.is_triton else 10
    return DisplayDevice(
        device_type=device_type,
        name=name,
        physical_address=physical_address,
        backlight=backlight,
        alarm_tone_duration_s=tone,
        lines=[
            DisplayLine(index=i, data_type=LineDataType.FREE)
            for i in range(1, device_type.max_lines + 1)
        ],
    )
