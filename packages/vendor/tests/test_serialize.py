"""Tests for the JSON project serialisation of a triton display config."""

from __future__ import annotations

import json

import pytest

from xknxeditor.vendor.triton import (
    DeviceType,
    DisplayDevice,
    DisplayLine,
    Eis6Scaling,
    LineDataType,
    NumberFormat,
    SendModeEis1,
    SendParameters,
    TimeDateFormat,
    default_device,
    from_json_bytes,
    to_json_bytes,
    validate_device,
)
from xknxeditor.vendor.triton.model import DateOrder


def _device() -> DisplayDevice:
    return DisplayDevice(
        device_type=DeviceType.TRITON_6325,
        name="Flur",
        physical_address="1.1.70",
        lines=[
            DisplayLine(
                index=5,
                data_type=LineDataType.VALUE_2BYTE_EIS5,
                text_off="INNEN",
                number_format=NumberFormat(
                    first_digit_position=7, integer_digits=2, decimal_digits=1
                ),
            ),
            DisplayLine(
                index=6,
                data_type=LineDataType.VALUE_1BYTE_EIS6,
                scaling=Eis6Scaling(display_at_0=0, display_at_255=100),
            ),
            DisplayLine(
                index=1,
                data_type=LineDataType.DATE_EIS4,
                time_date_format=TimeDateFormat(
                    show_weekday=True, date_order=DateOrder.MONTH_DAY_YEAR
                ),
            ),
            DisplayLine(
                index=2,
                data_type=LineDataType.VALUE_1BYTE_EIS6,
                send=True,
                send_parameters=SendParameters(
                    eis1_mode=SendModeEis1.OFF, eis6_min=1, eis6_max=99
                ),
            ),
        ],
    )


def test_json_round_trip() -> None:
    device = _device()
    blob = to_json_bytes(device)
    assert from_json_bytes(blob) == device


def test_json_is_valid_utf8_text() -> None:
    blob = to_json_bytes(_device())
    text = blob.decode("utf-8")
    assert '"device_type"' in text and '"lines"' in text


def test_default_device_is_valid_and_all_free() -> None:
    device = default_device(DeviceType.TRITON_6323, "Kitchen", "1.1.88")
    assert len(device.lines) == 5
    assert all(line.data_type is LineDataType.FREE for line in device.lines)
    assert validate_device(device) == []
    assert from_json_bytes(to_json_bytes(device)) == device


def test_default_device_6136up_has_eight_lines() -> None:
    device = default_device(DeviceType.INTERFACE_6136UP, "", "")
    assert len(device.lines) == 8


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        42,
        {"device_type": "triton-6325", "lines": [None]},
        {"device_type": "triton-6325", "lines": {}},
        {"device_type": "triton-6325", "schema": 2},
        {"device_type": "triton-6325", "beep_on_keypress": "False"},
        {"device_type": "triton-6325", "scroll_with_key": 1},
        {"device_type": "triton-6325", "key_backlight_blinks_on_alarm": "False"},
        {"device_type": "triton-6325", "name": None},
        {"device_type": "triton-6325", "lines": [{"index": 1.5}]},
        {"device_type": "triton-6325", "lines": [{"index": 1, "object_number": "2"}]},
        {
            "device_type": "6136UP",
            "lines": [{"index": 1, "send_parameters": {"eis5_min": float("nan")}}],
        },
    ],
)
def test_malformed_json_config_raises_value_error(payload: object) -> None:
    with pytest.raises(ValueError):
        from_json_bytes(json.dumps(payload).encode())
