"""Regression tests for the Busch-Jaeger triton LCD display blob viewer sections.

The display's full configuration lives only in the ``TritonLcd-Config`` BinaryData blob (UTF-8 JSON
from :mod:`xknxeditor.vendor.triton.serialize`), not in the KNX parameters. The reader surfaces it
as one "LCD Display" section grouped into a device node plus one node per line, and routes edits
back through :func:`apply_edit`.
"""

from __future__ import annotations

import json

import pytest
from editor_gui.plugin_blob_readers.triton_lcd import BLOB_NAME, apply_edit, read

from xknxeditor.vendor.triton.model import (
    DeviceType,
    DisplayLine,
    LineDataType,
    NumberFormat,
)
from xknxeditor.vendor.triton.serialize import default_device, to_json_bytes


def _config() -> bytes:
    device = default_device(DeviceType.TRITON_6325, "Flur", "1.1.70")
    # Line 1: a 2-byte float (EIS 5) temperature with a custom number format.
    device.lines[0] = DisplayLine(
        index=1,
        data_type=LineDataType.VALUE_2BYTE_EIS5,
        text_off="Temp",
        object_number=1,
        number_format=NumberFormat(integer_digits=2, decimal_digits=1),
    )
    # Line 2: a switch (EIS 1) with on/off texts.
    device.lines[1] = DisplayLine(
        index=2,
        data_type=LineDataType.SWITCH_EIS1,
        text_off="Off",
        text_on="On",
        object_number=2,
    )
    return to_json_bytes(device)


def _section(data: bytes):
    secs = read(data, name=BLOB_NAME)
    assert len(secs) == 1
    return secs[0]


def test_blob_name_constant() -> None:
    assert BLOB_NAME == "TritonLcd-Config"


def test_reads_device_group_and_one_group_per_line() -> None:
    section = _section(_config())
    assert section.section == "LCD Display"
    groups = {p.group for p in section.parameters}
    assert "Display" in groups
    line_groups = {g for g in groups if g.startswith("[")}
    # A triton 6325 has 5 lines, so 5 line nodes.
    assert len(line_groups) == 5
    # The EIS 5 line exposes number-format rows.
    line1_labels = [p.label for p in section.parameters if p.group_key == "line-1"]
    assert "First Digit Position" in line1_labels
    assert "Integer Digits" in line1_labels
    # The switch line exposes the "on" text.
    line2_labels = [p.label for p in section.parameters if p.group_key == "line-2"]
    assert "Text at On" in line2_labels


def test_device_group_rows_present() -> None:
    section = _section(_config())
    device_rows = {p.label: p for p in section.parameters if p.group_key == "device"}
    assert {"Name", "Physical Address", "Backlight", "Device Type"} <= set(device_rows)
    # A triton has the key-backlight-blinks option.
    assert "Key Backlight Blinks on Alarm" in device_rows
    assert device_rows["Name"].value == "Flur"
    assert device_rows["Physical Address"].value == "1.1.70"


def test_every_writable_row_is_routable_and_device_type_is_not() -> None:
    section = _section(_config())
    for p in section.parameters:
        if p.writable:
            assert p.ref_id.startswith("triton_lcd:"), p.label
    device_type = next(p for p in section.parameters if p.label == "Device Type")
    assert not device_type.writable
    assert not device_type.ref_id.startswith("triton_lcd:")
    assert device_type.value == "triton 6325"


def test_backlight_is_a_select_with_device_specific_choices() -> None:
    section = _section(_config())
    backlight = next(p for p in section.parameters if p.label == "Backlight")
    assert backlight.writable
    values = {raw for raw, _ in backlight.choices}
    # triton offers four backlight modes.
    assert values == {"always-on", "on-receive", "like-text-fields", "on-alarm"}


def test_noop_edit_is_byte_identical() -> None:
    data = _config()
    ref = f"triton_lcd:{BLOB_NAME}:line:2:text_off"
    row = next(
        p for s in read(data, name=BLOB_NAME) for p in s.parameters if p.ref_id == ref
    )
    assert apply_edit(data, ref, row.value) == data


def test_edit_text_off_round_trips() -> None:
    data = _config()
    ref = f"triton_lcd:{BLOB_NAME}:line:2:text_off"
    out = apply_edit(data, ref, "Aus")
    assert out != data
    again = next(
        p for s in read(out, name=BLOB_NAME) for p in s.parameters if p.ref_id == ref
    )
    assert again.value == "Aus"


def test_change_data_type_free_to_switch_shows_text_on() -> None:
    data = _config()
    ref = f"triton_lcd:{BLOB_NAME}:line:3:data_type"
    out = apply_edit(data, ref, LineDataType.SWITCH_EIS1.value)
    section = read(out, name=BLOB_NAME)[0]
    dt_row = next(p for p in section.parameters if p.ref_id == ref)
    assert dt_row.value == LineDataType.SWITCH_EIS1.value
    labels = [p.label for p in section.parameters if p.group_key == "line-3"]
    assert "Text at On" in labels


def test_change_data_type_free_to_eis6_shows_scaling_and_number_rows() -> None:
    data = _config()
    ref = f"triton_lcd:{BLOB_NAME}:line:4:data_type"
    out = apply_edit(data, ref, LineDataType.VALUE_1BYTE_EIS6.value)
    labels = [
        p.label
        for p in read(out, name=BLOB_NAME)[0].parameters
        if p.group_key == "line-4"
    ]
    assert "Display at 0" in labels and "Display at 255" in labels
    assert "First Digit Position" in labels


def test_edit_a_number_format_sub_field_round_trips() -> None:
    data = _config()
    ref = f"triton_lcd:{BLOB_NAME}:line:1:format:decimal_digits"
    out = apply_edit(data, ref, "2")
    again = next(
        p for s in read(out, name=BLOB_NAME) for p in s.parameters if p.ref_id == ref
    )
    assert again.value == "2"


def test_foreign_blob_returns_empty_and_apply_edit_raises() -> None:
    assert read(b"not json") == []
    assert read(b"\x00\x01\x02") == []
    with pytest.raises(ValueError):
        apply_edit(b"not json", f"triton_lcd:{BLOB_NAME}:device:name", "x")
    with pytest.raises(ValueError):
        apply_edit(_config(), "siemens_blob:foo", "x")
    with pytest.raises(ValueError):
        apply_edit(_config(), f"triton_lcd:{BLOB_NAME}:line:99:text_off", "x")


@pytest.mark.parametrize(
    "payload", [b"null", b"[]", b'{"device_type":"triton-6325","lines":[null]}']
)
def test_malformed_config_is_hidden_and_edit_rejected(payload: bytes) -> None:
    assert read(payload) == []
    with pytest.raises(ValueError):
        apply_edit(payload, f"triton_lcd:{BLOB_NAME}:device:name", "x")


@pytest.mark.parametrize(
    "suffix,value",
    [
        ("device:beep_on_keypress", "false"),
        ("device:beep_on_keypress", "1"),
        ("device:backlight", "always-off"),
        ("device:physical_address", "16.1.1"),
        ("device:physical_address", "1.1.256"),
        ("line:2:object_number", "-1"),
        ("line:2:object_number", "32"),
        ("line:2:receive", "no"),
        ("line:1:format:integer_digits", "9"),
        ("line:1:text_off", "x" * 17),
        ("line:2:send", "True"),
    ],
)
def test_invalid_edit_is_rejected(suffix: str, value: str) -> None:
    with pytest.raises(ValueError):
        apply_edit(_config(), f"triton_lcd:{BLOB_NAME}:{suffix}", value)


def test_foreign_blob_name_is_not_exposed_or_routed() -> None:
    assert read(_config(), name="Other") == []
    with pytest.raises(ValueError):
        apply_edit(_config(), "triton_lcd:Other:device:name", "x")
    assert all(
        p.ref_id.startswith(f"triton_lcd:{BLOB_NAME}:")
        for p in read(_config())[0].parameters
        if p.writable
    )


def test_noop_preserves_noncanonical_json_bytes() -> None:
    data = json.dumps(json.loads(_config()), indent=4, sort_keys=True).encode()
    assert apply_edit(data, f"triton_lcd:{BLOB_NAME}:device:name", "Flur") == data


def test_noop_on_implicit_subblock_default_preserves_bytes() -> None:
    data = apply_edit(
        _config(), f"triton_lcd:{BLOB_NAME}:line:3:data_type", "eis6-value"
    )
    assert (
        apply_edit(data, f"triton_lcd:{BLOB_NAME}:line:3:scaling:display_at_0", "0")
        == data
    )


def test_invalid_config_is_hidden_and_noop_does_not_bypass_validation() -> None:
    payload = json.loads(_config())
    payload["lines"][0]["text_off"] = "x" * 17
    data = json.dumps(payload).encode()
    assert read(data) == []
    with pytest.raises(ValueError):
        apply_edit(data, f"triton_lcd:{BLOB_NAME}:device:name", "Flur")
