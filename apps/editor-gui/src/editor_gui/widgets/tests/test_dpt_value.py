"""The value input for a group address: which input a DPT gets and how the entry is encoded."""

from __future__ import annotations

import pytest
from xknx.dpt.payload import DPTArray, DPTBinary

from editor_gui.dpt_formats import BitFormat, EnumFormat
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
