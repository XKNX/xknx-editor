"""The choices of an enumerated parameter type."""

from __future__ import annotations

from pathlib import Path

from xknxeditor.namespaces.intermediate import (
    ParameterType,
    ParameterTypeTypeRestriction,
    ParameterTypeTypeRestrictionBase,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_restriction_enumeration import (
    ParameterTypeTypeRestrictionEnumeration,
)
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.activation import shown_parameters
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
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


def test_choices_are_shared_by_parameter_type_across_placements_and_devices() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "activation_application.xml"
    (app,) = parse_application_xml(fixture.read_bytes(), "M-00FA")
    program, tree = app.resolved()
    default = DynamicUI(program, tree_builder=tree)
    enabled = DynamicUI(
        program,
        tree_builder=tree,
        parameter_values={f"{app.id}_P-1_R-1": "1", f"{app.id}_P-2_R-2": "1"},
    )
    default_params = {p.label: p for p in shown_parameters(default.ui()).values()}
    enabled_params = {p.label: p for p in shown_parameters(enabled.ui()).values()}
    assert "Visualisation" not in default_params
    visualisation = enabled_params["Visualisation"]
    mode = enabled_params["Mode"]
    function = enabled_params["Function"]
    assert (visualisation.value, mode.value) == ("1", "0")
    assert isinstance(visualisation.widget, EnumWidget)
    assert visualisation.widget is mode.widget is default_params["Mode"].widget
    assert [(c.value, c.label) for c in visualisation.widget.choices] == [
        (0, "None"),
        (1, "One Button"),
        (2, "Two Buttons"),
    ]
    assert isinstance(function.widget, EnumWidget)
    assert function.widget.choices is not visualisation.widget.choices
    assert [(c.value, c.label) for c in function.widget.choices] == [
        (0, "Switch"),
        (1, "Dimming"),
        (2, "Shutter"),
    ]
