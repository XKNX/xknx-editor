"""Enum choices shown as icons: MCP lists their icon, and one without a text (a colour) is
named by its icon, also to set it by ``value_label``."""

from __future__ import annotations

import pytest

from editor_gui.plugins.mcp.tools.project import (
    _enum_value_label,  # pyright: ignore[reportPrivateUsage]
    _resolve_enum_value,  # pyright: ignore[reportPrivateUsage]
    _widget_dict,  # pyright: ignore[reportPrivateUsage]
)
from xknxeditor.namespaces.intermediate.horizontal_alignment_t import (
    HorizontalAlignment,
)
from xknxeditor.prod.parser_v2.ui.parameter import (
    EnumChoice,
    EnumWidget,
    ParameterTypeTypeRestrictionBase,
)

_WIDGET = EnumWidget(
    (
        EnumChoice(0, "Theme Default", "e0"),
        EnumChoice(73, "Accept", "e73", icon="icon_03_accept"),
        EnumChoice(
            1, "", "e1", icon="color_red", picture_alignment=HorizontalAlignment.REPEAT
        ),
    ),
    ParameterTypeTypeRestrictionBase.VALUE,
)


def test_choices_carry_their_icons() -> None:
    assert _widget_dict(_WIDGET)["choices"] == [
        {"value": 0, "label": "Theme Default"},
        {"value": 73, "label": "Accept", "icon": "icon_03_accept"},
        {"value": 1, "label": "color_red", "icon": "color_red"},
    ]
    assert _enum_value_label(_WIDGET, "1") == "color_red"


def test_a_colour_is_set_by_its_icon_name() -> None:
    assert _resolve_enum_value(_WIDGET, "Color_Red", "Icon Color") == "1"
    with pytest.raises(ValueError, match="'color_red'"):
        _resolve_enum_value(_WIDGET, "blue", "Icon Color")
