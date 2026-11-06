"""Tests for the (reconstructed) triton display-config codec: round-trip + robustness.

The byte layout is unverified against hardware, so these tests only pin the codec's *internal*
contract: encode is deterministic, decode inverts encode exactly, and a foreign/corrupt blob is
rejected rather than silently mis-parsed.
"""

from __future__ import annotations

import pytest

from xknxeditor.vendor.triton import (
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
    TritonCodecError,
    decode_display,
    encode_display,
)


def _rich_device() -> DisplayDevice:
    return DisplayDevice(
        device_type=DeviceType.TRITON_6325,
        name="Flur",
        physical_address="1.1.70",
        backlight=BacklightMode.ON_ALARM,
        beep_on_keypress=True,
        scroll_with_key=True,
        key_backlight_blinks_on_alarm=True,
        lines=[
            DisplayLine(
                index=4,
                data_type=LineDataType.SWITCH_EIS1,
                text_off="LICHT AUS",
                text_on="LICHT EIN",
                object_number=15,
                alarm=True,
                receive=True,
            ),
            DisplayLine(
                index=5,
                data_type=LineDataType.VALUE_2BYTE_EIS5,
                text_off="INNEN      \xb0C",
                object_number=16,
                number_format=NumberFormat(
                    first_digit_position=7,
                    integer_digits=2,
                    decimal_digits=1,
                    extra_sign_position=True,
                    show_positive_sign=True,
                ),
            ),
            DisplayLine(
                index=6,
                data_type=LineDataType.VALUE_1BYTE_EIS6,
                text_off="DIMMER ### %",
                object_number=17,
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
                    eis1_mode=SendModeEis1.TOGGLE, eis6_min=10, eis6_max=200
                ),
            ),
        ],
    )


def test_round_trip_preserves_everything() -> None:
    device = _rich_device()
    blob = encode_display(device)
    again = decode_display(blob)
    assert again == device
    # encode is deterministic + idempotent through a decode.
    assert encode_display(again) == blob


def test_minimal_device_round_trips() -> None:
    device = DisplayDevice(device_type=DeviceType.INTERFACE_6136UP)
    assert decode_display(encode_display(device)) == device


def test_eis5_send_floats_round_trip() -> None:
    device = DisplayDevice(
        device_type=DeviceType.INTERFACE_6136UP,
        lines=[
            DisplayLine(
                index=1,
                data_type=LineDataType.VALUE_2BYTE_EIS5,
                send=True,
                send_parameters=SendParameters(eis5_min=1.5, eis5_max=25.0),
            )
        ],
    )
    out = decode_display(encode_display(device))
    sp = out.lines[0].send_parameters
    assert sp is not None and sp.eis5_min == 1.5 and sp.eis5_max == 25.0


def test_text_is_clamped_to_latin1_byte_length() -> None:
    # A >255-byte text cannot overflow the 1-byte length prefix; it is clamped.
    device = DisplayDevice(
        device_type=DeviceType.TRITON_6325,
        lines=[
            DisplayLine(index=1, data_type=LineDataType.STATIC_TEXT, text_off="a" * 300)
        ],
    )
    out = decode_display(encode_display(device))
    assert len(out.lines[0].text_off) == 255


def test_foreign_blob_is_rejected() -> None:
    with pytest.raises(TritonCodecError):
        decode_display(b"not a triton blob")
    with pytest.raises(TritonCodecError):
        decode_display(b"")


def test_truncated_blob_is_rejected() -> None:
    blob = encode_display(_rich_device())
    with pytest.raises(TritonCodecError):
        decode_display(blob[:10])
