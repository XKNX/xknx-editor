"""The value input for a group address: which input a DPT gets and how the entry is encoded."""

from __future__ import annotations

import pytest
from xknx.dpt.payload import DPTArray, DPTBinary

from editor_gui.dpt_formats import BitFormat, CompositeFormat, EnumFormat, FieldFormat
from editor_gui.widgets import dpt_value
from editor_gui.widgets.dpt_value import DptValueInput, dpt_label


@pytest.fixture(autouse=True)
def _point_decimal(monkeypatch: pytest.MonkeyPatch) -> None:
    from editor_gui.regional import RegionalFormat

    monkeypatch.setattr(
        dpt_value, "regional_format", lambda: RegionalFormat(decimal=",", group=".")
    )


def _editor(token: str, formats: dict | None = None):  # type: ignore[type-arg]
    editor = dpt_value._editor_for(token, formats or {})
    assert editor is not None
    return editor


def test_bit_type_offers_the_master_data_texts() -> None:
    editor = _editor("DPST-1-8", {"DPST-1-8": BitFormat("Up", "Down")})
    assert [label for label, _ in editor.options] == ["Up", "Down"]
    editor.selected = 1
    assert editor.payload() == DPTBinary(1)


def test_bit_type_without_master_data_uses_the_xknx_values() -> None:
    editor = _editor("DPST-1-1")
    assert [label for label, _ in editor.options] == ["Off", "On"]
    assert editor.payload() == DPTBinary(0)


def test_enumeration_offers_every_value() -> None:
    editor = _editor(
        "DPST-20-102", {"DPST-20-102": EnumFormat({1: "Comfort", 0: "Auto"})}
    )
    assert [label for label, _ in editor.options] == ["Auto", "Comfort"]
    editor.selected = 1
    assert editor.payload() == DPTArray((1,))


def test_number_accepts_the_regional_decimal_and_checks_the_range() -> None:
    editor = _editor("DPST-9-1")
    assert editor.unit == "°C"
    editor.text = "21,5"
    assert editor.payload() == DPTArray((0x0C, 0x33))
    editor.text = str(int(editor.transcoder.value_max) + 1)
    with pytest.raises(ValueError, match=str(int(editor.transcoder.value_max))):
        editor.payload()
    editor.text = "warm"
    with pytest.raises(ValueError):
        editor.payload()


def test_integer_types_want_whole_numbers() -> None:
    editor = _editor("DPST-7-1")
    editor.text = "2,5"
    with pytest.raises(ValueError):
        editor.payload()
    editor.text = "65535"
    assert editor.payload() == DPTArray((0xFF, 0xFF))


def test_text_is_limited_to_the_type_length() -> None:
    editor = _editor("DPST-16-0")
    editor.text = "x" * 15
    with pytest.raises(ValueError, match="14"):
        editor.payload()
    editor.text = "Hallo"
    assert editor.payload().value[:5] == tuple(b"Hallo")


def test_compound_type_has_one_input_per_field() -> None:
    editor = _editor("DPST-3-7")
    assert [f.name for f in editor.fields] == ["control", "step_code"]
    control, step = editor.fields
    control.selected = control.options.index("increase")
    step.text = "3"
    assert editor.payload() == DPTBinary(0b1011)
    step.text = "9"
    with pytest.raises(ValueError):
        editor.payload()


def test_value_seen_on_the_bus_prefills_the_input() -> None:
    value = DptValueInput("test")
    value.set_payload("DPST-10-1", {}, DPTArray((12, 5, 0)))
    payload = value.payload()
    assert payload == DPTArray((12, 5, 0))


def test_unknown_type_cannot_be_written() -> None:
    value = DptValueInput("test")
    assert not value.can_write(None, {})
    assert value.payload() is None
    assert value.error


def test_dpt_label() -> None:
    assert dpt_label("DPST-1-1") == "1.001 Switch"
    assert dpt_label(None) == ""


def test_type_without_limits_has_no_range_hint() -> None:
    editor = _editor("DPST-14-56")
    assert (
        dpt_value._range_hint(editor.transcoder.value_min, editor.transcoder.value_max)
        == ""
    )
    assert dpt_value._range_hint(-273.0, 670760.0) == "-273 … 670760"
    editor.text = "inf"
    with pytest.raises(ValueError):
        editor.payload()
    editor.text = "1,5"
    assert editor.payload() == DPTArray((0x3F, 0xC0, 0x00, 0x00))


def _layout(*fields: FieldFormat) -> dict[str, CompositeFormat]:
    return {"DPST-99-1": CompositeFormat(fields)}


def test_utf8_string_without_xknx_class_is_text() -> None:
    layout = {
        "DPST-28-1": CompositeFormat(
            (
                FieldFormat(
                    "string",
                    312,
                    encoding="utf-8",
                    variable_length=True,
                    null_terminated=True,
                ),
            )
        )
    }
    editor = _editor("DPST-28-1", layout)
    editor.fields[0].text = "Grüße"
    assert editor.payload() == DPTArray("Grüße".encode() + b"\x00")
    editor.fields[0].text = "x" * 39
    with pytest.raises(ValueError, match="39"):
        editor.payload()


def test_master_layout_packs_fields_most_significant_first() -> None:
    # DPT 26.001 scene information: reserved bit, active flag, 6-bit scene number.
    layout = _layout(
        FieldFormat("reserved", 1),
        FieldFormat("bit", 1, cleared="scene is active", set="scene is inactive"),
        FieldFormat("unsigned", 6, name="SceneNumber", minimum=0, maximum=63),
    )
    editor = _editor("DPST-99-1", layout)
    _, flag, scene = editor.fields
    flag.selected = 1
    scene.text = "5"
    assert editor.payload() == DPTArray((0x45,))
    scene.text = "64"
    with pytest.raises(ValueError, match="Scene Number"):
        editor.payload()
    scene.text = ""
    flag.selected = 0
    editor.set_payload(DPTArray((0x45,)))
    assert (flag.selected, scene.text) == (1, "5")


def test_master_layout_scales_and_encodes_floats() -> None:
    layout = _layout(
        FieldFormat("unsigned", 8, name="S", unit="%", coefficient=0.3921566),
        FieldFormat("float", 16, name="Temp", unit="°C"),
    )
    editor = _editor("DPST-99-1", layout)
    saturation, temperature = editor.fields
    saturation.text = "100"
    temperature.text = "21,5"
    assert editor.payload() == DPTArray((0xFF, 0x0C, 0x33))


@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("index of access identification code", "Index of Access Identification Code"),
        ("SceneNumber", "Scene Number"),
        ("DHW mode", "DHW Mode"),
        ("step code", "Step Code"),
    ],
)
def test_title_case(text: str, title: str) -> None:
    assert dpt_value.title_case(text) == title


def test_unnamed_values_are_numbered() -> None:
    digit = FieldFormat("unsigned", 4, minimum=0, maximum=9)
    layout = _layout(
        digit,
        digit,
        FieldFormat("bit", 1, name="read direction", cleared="l", set="r"),
        FieldFormat("reserved", 3),
    )
    editor = _editor("DPST-99-1", layout)
    assert [f.label for f in editor.fields] == [
        "Value 1",
        "Value 2",
        "Read Direction",
        "",
    ]


def test_dpt_label_prefers_the_master_data_name() -> None:
    names = {"DPST-15-0": "entrance access", "DPT-21": "8-bit set"}
    assert dpt_label("DPST-15-0", names) == "15.000 Entrance Access"
    assert dpt_label("DPT-21", names) == "21.xxx 8-bit Set"


def test_value_texts_start_upper_case() -> None:
    editor = _editor("DPST-1-2", {"DPST-1-2": BitFormat("not accepted", "accepted")})
    assert [label for label, _ in editor.options] == ["Not accepted", "Accepted"]
    assert dpt_value.value_text("DHW mode") == "DHW mode"
