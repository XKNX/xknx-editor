"""The choices of an enumerated parameter type."""

from __future__ import annotations

from xknxeditor.namespaces.intermediate import (
    ParameterType,
    ParameterTypeTypeRestriction,
    ParameterTypeTypeRestrictionBase,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_restriction_enumeration import (
    ParameterTypeTypeRestrictionEnumeration,
)
from xknxeditor.prod.parser_v2.ui.parameter import EnumWidget, resolve_widget


def _enum(value: int, text: str | None) -> ParameterTypeTypeRestrictionEnumeration:
    return ParameterTypeTypeRestrictionEnumeration(
        text=text, value=value, id=f"PT-1_EN-{value}"
    )


def test_an_entry_without_text_shows_empty() -> None:
    restriction = ParameterTypeTypeRestriction(
        enumeration=[_enum(0, ""), _enum(1, "1 Page"), _enum(2, None)],
        base=ParameterTypeTypeRestrictionBase.VALUE,
        size_in_bit=8,
    )
    widget = resolve_widget(ParameterType(choice=restriction, id="PT-1", name="P"))
    assert isinstance(widget, EnumWidget)
    assert [(c.value, c.label) for c in widget.choices] == [
        (0, ""),
        (1, "1 Page"),
        (2, ""),
    ]
