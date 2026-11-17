"""Display units and stored values are separate contracts."""

from dataclasses import replace

import pytest

from xknxeditor.vendor.siemens_generic import (
    build_view,
    parse_description,
    parse_settings,
)
from xknxeditor.vendor.siemens_generic.configuration import default_config, edit
from xknxeditor.vendor.siemens_generic.description import TypeKind
from xknxeditor.vendor.siemens_generic.image import compile_image
from xknxeditor.vendor.siemens_generic.presentation import ValueFormat, value_format


@pytest.mark.parametrize(
    ("kind", "clock", "raw", "shown", "entered", "stored"),
    [
        (TypeKind.TIME_S, "mm:ss", "900", "15:00", "02:30", "150"),
        (TypeKind.TIME_S, "mm", "900", "15", "2", "120"),
        (TypeKind.TIME_S, "ss", "1", "01", "2", "2"),
        (TypeKind.TIME_S, "H:mm", "300", "0:05", "1:02", "3720"),
        (TypeKind.TIME_S, "H:mm:ss", "300", "0:05:00", "1:02:03", "3723"),
        (TypeKind.TIME_MS, "HH:mm:ss", "1800000", "00:30:00", "00:00:02", "2000"),
        (TypeKind.TIME_MS, "mm:ss.f", "1500", "00:01.5", "00:02,5", "2500"),
        (
            TypeKind.TIME_MS,
            "HH:mm:ss.f",
            "1234",
            "00:00:01.234",
            "00:00:02.345",
            "2345",
        ),
    ],
)
def test_clock_display_and_edit(kind, clock, raw, shown, entered, stored):
    formatting = ValueFormat(kind, clock=clock)
    assert formatting.display(raw) == shown
    assert formatting.parse(shown, raw) == raw
    assert formatting.parse(entered, raw) == stored


def test_percent_rounding_and_noop_preserve_every_byte():
    formatting = ValueFormat(TypeKind.NUMBER, format="Percent")
    assert formatting.display("13") == "5"
    assert formatting.parse("10", "13") == "25"
    assert formatting.parse("50", "13") == "127"
    assert formatting.parse("100", "13") == "255"
    for raw in range(256):
        assert formatting.parse(formatting.display(str(raw)), str(raw)) == str(raw)
        assert formatting.parse(formatting.display(str(raw)) + ".0", str(raw)) == str(
            raw
        )
    assert formatting.parse("5", "0x0D") == "0x0D"
    for invalid in ("nan", "inf", "-1", "101", "text"):
        with pytest.raises(ValueError):
            formatting.parse(invalid, "13")


def test_time_validation_rejects_wrong_format_range_and_precision():
    formatting = ValueFormat(
        TypeKind.TIME_S, clock="mm:ss", pattern=r"(?:[0-2]?\d:[0-5]\d|30:00)"
    )
    for invalid in ("900", "01:60", "31:00", "00:00.001", "-1:00", "nan:00"):
        with pytest.raises(ValueError):
            formatting.parse(invalid, "900")
    assert formatting.parse("30:00", "900") == "1800"
    with pytest.raises(ValueError, match="Unsupported"):
        replace(formatting, clock="days").display("900")


def test_percent_casing_and_restricted_range_use_display_units():
    formatting = ValueFormat(TypeKind.NUMBER, format="percent", minimum=0, maximum=200)
    assert formatting.display("13") == "5"
    assert formatting.parse("78", "13") == "199"
    with pytest.raises(ValueError, match="between 0 and 78"):
        formatting.parse("90", "13")


def test_minute_display_is_compact_and_preserves_partial_minutes():
    formatting = ValueFormat(TypeKind.TIME_S, clock="mm")
    assert formatting.display("100") == "1.666667"
    assert formatting.parse("1.666667", "100") == "100"
    assert formatting.display("0") == "0"


def test_description_format_controls_edit_and_image_independently_of_labels():
    description = parse_description(b"""<ApplicationProgram Id="demo"><Static>
      <CodeSegments><CodeSegment Id="s" Address="4096"/></CodeSegments>
      <ParameterTypes><ParameterType Id="t"><TypeTimeSec Size="16"/></ParameterType></ParameterTypes>
      <Parameters><Parameter Id="p" RefId="t" Value="900" Text="PWM" FormatString="ss">
        <Memory CodeSegment="s" Offset="0"/></Parameter></Parameters>
      <ParameterRefs><ParameterRef Id="r" RefId="p" FormatString="mm" RegEx="[0-9]+"/></ParameterRefs>
      </Static><Dynamic><Channel Id="c"><ParameterBlock Id="b"><ParameterRefRef RefId="r"/>
      </ParameterBlock></Channel></Dynamic></ApplicationProgram>""")
    assert description is not None
    description = replace(description, allocation_size=2)
    formatting = value_format(description, "r")
    assert formatting.clock == "mm"
    before = default_config(description)
    after = edit(description, before, "r", formatting.parse("2", "900"))
    settings = parse_settings(after)
    assert settings is not None and settings.values["r"] == "120"
    assert compile_image(description, settings).data[:2] == b"\x00\x78"
    row = build_view(description, settings).pages[0].rows[0]
    assert row.value_text == "2 (120 s)" and row.clock
