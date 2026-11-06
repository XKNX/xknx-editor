"""Tests for the triton display config model: validation rules and device capabilities."""

from __future__ import annotations

import pytest

from xknxeditor.vendor.triton import (
    BacklightMode,
    DeviceType,
    DisplayDevice,
    DisplayLine,
    Eis6Scaling,
    LineDataType,
    NumberFormat,
    backlight_modes,
    data_types_for,
    device_type_from_app_id,
    device_type_from_text,
    validate_device,
)


def test_device_line_counts_and_send_capability() -> None:
    assert DeviceType.INTERFACE_6136UP.max_lines == 8
    assert DeviceType.TRITON_6323.max_lines == 5
    assert DeviceType.TRITON_6325.max_lines == 5
    assert DeviceType.INTERFACE_6136UP.has_send_function
    assert not DeviceType.TRITON_6325.has_send_function
    assert DeviceType.TRITON_6323.is_triton
    assert not DeviceType.INTERFACE_6136UP.is_triton


def test_eis_to_dpt_mapping() -> None:
    assert LineDataType.SWITCH_EIS1.dpt == "1.001"
    assert LineDataType.VALUE_1BYTE_EIS6.dpt == "5.001"
    assert LineDataType.VALUE_2BYTE_EIS5.dpt == "9.001"
    assert LineDataType.TIME_EIS3.dpt == "10.001"
    assert LineDataType.DATE_EIS4.dpt == "11.001"
    assert LineDataType.STATIC_TEXT.dpt is None
    assert LineDataType.VALUE_2BYTE_EIS5.object_size_bits == 16
    assert LineDataType.FREE.object_size_bits is None


def test_data_types_time_date_triton_only_and_6136up_line8() -> None:
    triton = data_types_for(DeviceType.TRITON_6325, 1)
    assert LineDataType.TIME_EIS3 in triton and LineDataType.DATE_EIS4 in triton
    up = data_types_for(DeviceType.INTERFACE_6136UP, 1)
    assert LineDataType.TIME_EIS3 not in up
    assert LineDataType.VALUE_2BYTE_EIS5 in up
    # 6136UP line 8: no 2-byte value
    up8 = data_types_for(DeviceType.INTERFACE_6136UP, 8)
    assert LineDataType.VALUE_2BYTE_EIS5 not in up8
    assert LineDataType.VALUE_1BYTE_EIS6 in up8


def test_device_type_detection_from_app_id_and_name() -> None:
    # App id (brand-agnostic): BJ 0x6325 / 0x6323, ABB numbers a 3-fold LCD as 0x6322.
    assert device_type_from_app_id("M-0007_A-6325-12-ABCD") is DeviceType.TRITON_6325
    assert device_type_from_app_id("M-0007_A-6323-09-1234") is DeviceType.TRITON_6323
    assert device_type_from_app_id("M-0002_A-6322-07-5EC0") is DeviceType.TRITON_6323
    assert device_type_from_app_id("M-0007_A-6324-03-0001") is None  # non-LCD triton
    assert device_type_from_app_id("not-an-app-id") is None
    # Name fallback (product naming).
    assert (
        device_type_from_text("6931+6325 5f-triton mit LCD") is DeviceType.TRITON_6325
    )
    assert device_type_from_text("6136UP") is DeviceType.INTERFACE_6136UP
    assert device_type_from_text("switch actuator") is None


def test_backlight_modes_differ_by_device() -> None:
    assert backlight_modes(DeviceType.TRITON_6325) == (
        BacklightMode.ALWAYS_ON,
        BacklightMode.ON_RECEIVE,
        BacklightMode.LIKE_TEXT_FIELDS,
        BacklightMode.ON_ALARM,
    )
    assert backlight_modes(DeviceType.INTERFACE_6136UP) == (
        BacklightMode.ALWAYS_OFF,
        BacklightMode.ALWAYS_ON,
        BacklightMode.ON_RECEIVE,
    )


def test_valid_device_has_no_issues() -> None:
    device = DisplayDevice(
        device_type=DeviceType.TRITON_6325,
        name="Flur",
        physical_address="1.1.70",
        lines=[
            DisplayLine(
                index=5,
                data_type=LineDataType.VALUE_2BYTE_EIS5,
                text_off="INNEN      \xb0C",
                number_format=NumberFormat(
                    first_digit_position=7, integer_digits=2, decimal_digits=1
                ),
            ),
        ],
    )
    assert validate_device(device) == []


def test_time_line_on_6136up_is_rejected() -> None:
    device = DisplayDevice(
        device_type=DeviceType.INTERFACE_6136UP,
        lines=[DisplayLine(index=1, data_type=LineDataType.TIME_EIS3)],
    )
    issues = validate_device(device)
    assert any("Time and date" in m for m in issues)


def test_bad_number_format_and_scaling_are_reported() -> None:
    device = DisplayDevice(
        device_type=DeviceType.TRITON_6325,
        lines=[
            DisplayLine(
                index=1,
                data_type=LineDataType.VALUE_1BYTE_EIS6,
                number_format=NumberFormat(
                    integer_digits=9, show_positive_sign=True, extra_sign_position=False
                ),
                scaling=Eis6Scaling(display_at_0=100, display_at_255=0),
            )
        ],
    )
    issues = validate_device(device)
    assert any("Integer digits" in m for m in issues)
    assert any("positive sign" in m for m in issues)
    assert any("display at 0 must be below" in m for m in issues)


def test_alarm_requires_switch_and_receive() -> None:
    device = DisplayDevice(
        device_type=DeviceType.TRITON_6325,
        lines=[
            DisplayLine(
                index=1,
                data_type=LineDataType.VALUE_1BYTE_EIS6,
                alarm=True,
                receive=False,
            )
        ],
    )
    issues = validate_device(device)
    assert any("alarm line must be a switch" in m for m in issues)
    assert any("alarm line must also receive" in m for m in issues)


def test_duplicate_line_index_is_reported() -> None:
    device = DisplayDevice(
        device_type=DeviceType.TRITON_6325,
        lines=[
            DisplayLine(index=1, data_type=LineDataType.SWITCH_EIS1),
            DisplayLine(index=1, data_type=LineDataType.SWITCH_EIS1),
        ],
    )
    assert any("configured more than once" in m for m in validate_device(device))


def test_text_too_long_and_text_on_only_for_switch() -> None:
    device = DisplayDevice(
        device_type=DeviceType.TRITON_6325,
        lines=[
            DisplayLine(
                index=1,
                data_type=LineDataType.VALUE_1BYTE_EIS6,
                text_off="x" * 17,
                text_on="on",
            )
        ],
    )
    issues = validate_device(device)
    assert any("exceeds 16" in m for m in issues)
    assert any("Text at on is only used by a switch" in m for m in issues)


@pytest.mark.parametrize(
    "app_id",
    [
        "M-0007_A-6322-07-5EC0",
        "M-FFFF_A-6325-12-ABCD",
        "M-0007_A-63250-12-ABCD",
        "prefix_M-0007_A-6325-12-ABCD",
        "M-0007_A-6136-12-ABCD",
    ],
)
def test_unrelated_application_is_not_a_triton_display(app_id: str) -> None:
    assert device_type_from_app_id(app_id) is None


@pytest.mark.parametrize("name", ["SmartTouch 6136", "163250", "263230", "6136UPgrade"])
def test_unrelated_product_text_is_not_a_display(name: str) -> None:
    assert device_type_from_text(name) is None
