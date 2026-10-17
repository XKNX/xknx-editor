from editor_gui.plugins.project.ga_name_template import (
    DEFAULT_GA_NAME_TEMPLATE,
    PLACEHOLDERS,
    render_name_template,
)

_FULL = {
    "building": "Haus",
    "floor": "EG",
    "room": "Flur",
    "device": "Taster",
    "devicename": "Taster",
    "devicedescription": "",
    "product": "BE-TAS",
    "manufacturer": "MDT",
    "order": "BE-TAS8",
    "address": "1.1.5",
    "object": "TW 1",
    "ganame": "TW 1",
    "function": "Switch",
    "number": "3",
    "dpt": "1.001",
}


def test_default_template_reproduces_room_device_object_function() -> None:
    assert (
        render_name_template(DEFAULT_GA_NAME_TEMPLATE, _FULL)
        == "Flur-Taster-TW 1-Switch"
    )


def test_no_space_template_keeps_tight_separators_on_empty_middle() -> None:
    values = {**_FULL, "device": ""}
    assert render_name_template("{room}-{device}-{object}", values) == "Flur-TW 1"


def test_space_template_keeps_spaced_separators_on_empty_middle() -> None:
    values = {**_FULL, "device": ""}
    assert render_name_template("{room} - {device} - {object}", values) == "Flur - TW 1"


def test_empty_leading_placeholder_drops_its_separator() -> None:
    values = {**_FULL, "room": ""}
    assert (
        render_name_template("{room} - {device} - {object}", values) == "Taster - TW 1"
    )


def test_empty_middle_placeholder_collapses_separators() -> None:
    values = {**_FULL, "device": ""}
    assert render_name_template("{room} - {device} - {object}", values) == "Flur - TW 1"


def test_empty_trailing_placeholder_drops_its_separator() -> None:
    values = {**_FULL, "function": ""}
    assert render_name_template("{object} - {function}", values) == "TW 1"


def test_slash_template_with_empty_part() -> None:
    assert (
        render_name_template("{building}/{room}: {object}", {**_FULL, "building": ""})
        == "Flur: TW 1"
    )


def test_unknown_placeholder_is_blank_not_an_error() -> None:
    assert render_name_template("{nope} {object}", _FULL) == "TW 1"


def test_all_empty_yields_empty_string() -> None:
    assert render_name_template(DEFAULT_GA_NAME_TEMPLATE, {}) == ""


def test_ganame_alias_equals_object() -> None:
    assert render_name_template("{ganame}", _FULL) == "TW 1"


def test_placeholders_cover_the_default_template_tokens() -> None:
    import re

    for name in re.findall(r"\{(\w+)\}", DEFAULT_GA_NAME_TEMPLATE):
        assert name in PLACEHOLDERS


def test_format_spec_zero_pads_numeric_value() -> None:
    assert render_name_template("{object} {n:02}", {**_FULL, "n": 3}) == "TW 1 03"


def test_format_spec_on_text_value_falls_back() -> None:
    assert render_name_template("{object:02}", _FULL) == "TW 1"
