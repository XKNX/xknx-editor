"""Legacy (ETS2/3-origin) products can omit numeric bounds the binding requires; they are healed."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from xknxeditor.namespaces.intermediate.com_object_size_t import ComObjectSize
from xknxeditor.namespaces.intermediate.parameter_type_t_type_float import (
    ParameterTypeTypeFloat,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_number import (
    ParameterTypeTypeNumber,
)
from xknxeditor.namespaces.intermediate.parameter_type_t_type_time import (
    ParameterTypeTypeTime,
)
from xknxeditor.prod import parse_application_xml
from xknxeditor.prod.heal import heal_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.ui import UiComObject, UiNode, UiParameterBlock, UiTab


def _application_xml(parameter_type: str) -> bytes:
    return (
        b'<?xml version="1.0" encoding="utf-8"?>\n'
        b'<KNX xmlns="http://knx.org/xml/project/14">'
        b'<ManufacturerData><Manufacturer RefId="M-0007">'
        b"<ApplicationPrograms>"
        b'<ApplicationProgram Id="M-0007_A-1234-11-ABCD" ApplicationNumber="1" '
        b'ApplicationVersion="1" ProgramType="ApplicationProgram" MaskVersion="MV-0701" '
        b'Name="Legacy" LoadProcedureStyle="DefaultProcedure" PeiType="0" '
        b'DefaultLanguage="en-US" DynamicTableManagement="true" Linkable="false">'
        b"<Static><ParameterTypes>"
        b'<ParameterType Id="M-0007_A-1234-11-ABCD_PT-x" Name="x">'
        + parameter_type.encode("ascii")
        + b"</ParameterType>"
        b"</ParameterTypes></Static>"
        b"</ApplicationProgram></ApplicationPrograms>"
        b"</Manufacturer></ManufacturerData></KNX>"
    )


def _number(app: object) -> ParameterTypeTypeNumber:
    program = app.program  # type: ignore[attr-defined]
    choice = program.static.parameter_types.parameter_type[0].choice
    assert isinstance(choice, ParameterTypeTypeNumber)
    return choice


def test_heal_returns_same_object_when_bounds_present() -> None:
    conformant = _application_xml(
        '<TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />'
    )
    assert heal_application_xml(conformant) is conformant


def test_heal_is_noop_without_bounded_elements() -> None:
    xml = b'<KNX xmlns="http://knx.org/xml/project/14"><Foo/></KNX>'
    assert heal_application_xml(xml) is xml


def test_number_missing_both_bounds_gets_defaults_from_size() -> None:
    (app,) = parse_application_xml(
        _application_xml('<TypeNumber SizeInBit="8" Type="unsignedInt" />'), "M-0007"
    )
    number = _number(app)
    assert number.min_inclusive == 0
    assert number.max_inclusive == 255  # 2**8 - 1


def test_number_missing_only_min_keeps_existing_max() -> None:
    (app,) = parse_application_xml(
        _application_xml(
            '<TypeNumber SizeInBit="32" Type="signedInt" maxInclusive="214748364" />'
        ),
        "M-0007",
    )
    number = _number(app)
    assert number.min_inclusive == 0
    assert number.max_inclusive == 214748364


def test_float_missing_bounds_gets_fallback_default() -> None:
    (app,) = parse_application_xml(
        _application_xml('<TypeFloat Encoding="DPT 9" />'), "M-0007"
    )
    choice = app.program.static.parameter_types.parameter_type[0].choice
    assert isinstance(choice, ParameterTypeTypeFloat)
    assert choice.min_inclusive == 0
    assert choice.max_inclusive == 2147483647


def test_time_missing_bounds_gets_defaults_from_size() -> None:
    (app,) = parse_application_xml(
        _application_xml('<TypeTime SizeInBit="16" Unit="Milliseconds" />'), "M-0007"
    )
    choice = app.program.static.parameter_types.parameter_type[0].choice
    assert isinstance(choice, ParameterTypeTypeTime)
    assert choice.min_inclusive == 0
    assert choice.max_inclusive == 65535  # 2**16 - 1


def _group_objects(nodes: Sequence[UiNode]) -> list[UiComObject]:
    out: list[UiComObject] = []
    for node in nodes:
        if isinstance(node, UiComObject):
            out.append(node)
        elif isinstance(node, (UiTab, UiParameterBlock)):
            out.extend(_group_objects(node.children))
    return out


_VARIABLE_XML = (
    Path(__file__).parent / "fixtures" / "variable_object_size_application.xml"
).read_bytes()


def test_an_object_size_variable_is_read_as_legacy_var_data() -> None:
    healed = heal_application_xml(_VARIABLE_XML)
    assert b'ObjectSize="Variable"' not in healed
    assert b"ObjectSize='Variable'" not in healed
    assert healed.count(b'ObjectSize="LegacyVarData"') == 1
    assert healed.count(b"ObjectSize='LegacyVarData'") == 1
    # The other sizes and the rest of the file stay as they are.
    assert len(healed) == len(_VARIABLE_XML) + 2 * len("LegacyVarData") - 2 * len(
        "Variable"
    )
    assert b'ObjectSize="2 Bytes"' in healed


def test_heal_leaves_other_variable_texts_alone() -> None:
    xml = (
        b'<KNX xmlns="http://knx.org/xml/project/14">'
        b'<LdCtrlSetControlVariable Name="EnableSegmentWrite" Value="true" />'
        b'<ComObject Name="Variable" Text="Variable" ObjectSize="1 Bit" /></KNX>'
    )
    assert heal_application_xml(xml) is xml


def test_group_objects_of_the_variable_size_are_built() -> None:
    (app,) = parse_application_xml(_VARIABLE_XML, "M-00FA")
    sizes = {
        co.id: co.object_size
        for co in app.program.static.com_object_table.com_object  # type: ignore[union-attr]
    }
    assert sizes["M-00FA_A-0007-01-0000_O-1"] is ComObjectSize.LEGACY_VAR_DATA
    assert sizes["M-00FA_A-0007-01-0000_O-3"] is ComObjectSize.VALUE_2_BYTES
    objects = {co.number: co for co in _group_objects(DynamicUI(app.program).ui())}
    # The object's own size and the size its reference gives.
    assert objects[1].object_size == "LegacyVarData"
    assert objects[2].object_size == "LegacyVarData"
    assert objects[3].object_size == "2 Bytes"
