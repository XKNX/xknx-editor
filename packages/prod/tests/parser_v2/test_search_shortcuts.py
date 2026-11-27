"""What a recovery search asks of a UI thousands of times is told the short way, the same as
the long way: the values of a few parameters decoded at their fields alone, the scope of a
ref told by the ids of the module instances, the active refs of an instance, and a value
written into memory as its type wrote it before."""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.prod.errors import EncodingError
from xknxeditor.prod.parser_v2 import encode
from xknxeditor.prod.parser_v2.activation import shown_parameters, values_to_try
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.parser_v2.state import ModuleState

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_APP = "M-00FA_A-00FF-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
# Settings in memory: plain ones, one behind a Choose, and the members of a union.
_MEMORY_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="255" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-0701" Name="Memory"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{_SEGMENT}" Address="256" Size="6" /></Code>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-Mode" Name="Mode"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Off" Value="0" Id="{_APP}_PT-Mode_EN-0" />
<Enumeration Text="On" Value="1" Id="{_APP}_PT-Mode_EN-1" />
<Enumeration Text="Both" Value="2" Id="{_APP}_PT-Mode_EN-2" />
</TypeRestriction></ParameterType>
<ParameterType Id="{_APP}_PT-N8" Name="N8"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="200" /></ParameterType>
<ParameterType Id="{_APP}_PT-N4" Name="N4"><TypeNumber SizeInBit="4" Type="unsignedInt" minInclusive="0" maxInclusive="15" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Mode" ParameterType="{_APP}_PT-Mode" Text="Mode" Value="0">
<Memory CodeSegment="{_SEGMENT}" Offset="0" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-2" Name="Level" ParameterType="{_APP}_PT-N8" Text="Level" Value="7">
<Memory CodeSegment="{_SEGMENT}" Offset="1" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-3" Name="Hidden" ParameterType="{_APP}_PT-Mode" Text="Hidden" Value="1">
<Memory CodeSegment="{_SEGMENT}" Offset="5" BitOffset="0" /></Parameter>
<Union SizeInBit="16"><Memory CodeSegment="{_SEGMENT}" Offset="2" BitOffset="0" />
<Parameter Id="{_APP}_UP-1" Name="Kind" ParameterType="{_APP}_PT-Mode" Offset="0" BitOffset="0" Text="Kind" Value="0" DefaultUnionParameter="true" />
<Parameter Id="{_APP}_UP-2" Name="Low" ParameterType="{_APP}_PT-N4" Offset="1" BitOffset="4" Text="Low" Value="3" />
<Parameter Id="{_APP}_UP-3" Name="Wide" ParameterType="{_APP}_PT-N8" Offset="0" BitOffset="0" Text="Wide" Value="9" />
</Union>
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_APP}_P-1_R-1" RefId="{_APP}_P-1" />
<ParameterRef Id="{_APP}_P-2_R-2" RefId="{_APP}_P-2" />
<ParameterRef Id="{_APP}_P-3_R-3" RefId="{_APP}_P-3" />
<ParameterRef Id="{_APP}_UP-1_R-4" RefId="{_APP}_UP-1" />
<ParameterRef Id="{_APP}_UP-2_R-5" RefId="{_APP}_UP-2" />
<ParameterRef Id="{_APP}_UP-3_R-6" RefId="{_APP}_UP-3" />
</ParameterRefs>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="General">
<ParameterRefRef RefId="{_APP}_P-1_R-1" />
<ParameterRefRef RefId="{_APP}_P-2_R-2" />
<choose ParamRefId="{_APP}_P-1_R-1">
<when test="1"><ParameterRefRef RefId="{_APP}_P-3_R-3" /><ParameterRefRef RefId="{_APP}_UP-1_R-4" /></when>
<when test="2"><ParameterRefRef RefId="{_APP}_UP-3_R-6" /><ParameterRefRef RefId="{_APP}_UP-2_R-5" /></when>
</choose>
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""
_MEMORY = "memory"
_APPLICATIONS = [
    _MEMORY,
    *sorted(path.name for path in _FIXTURES.glob("*_application.xml")),
]


def _application(name: str) -> Application:
    if name == _MEMORY:
        return parse_application_xml(_MEMORY_XML.encode(), "M-00FA")[0]
    data = (_FIXTURES / name).read_bytes()
    manufacturer = data.split(b'Manufacturer RefId="', 1)[1].split(b'"', 1)[0]
    return parse_application_xml(data, manufacturer.decode())[0]


def _ui(application: Application, values: dict[str, str]) -> DynamicUI:
    program, builder = application.resolved()
    return DynamicUI(program, tree_builder=builder, parameter_values=values)


def _states(application: Application) -> list[DynamicUI]:
    """The UI with the defaults, and with each other value of each setting shown with them."""
    uis = [_ui(application, {})]
    for ref_id, parameter in shown_parameters(uis[0].ui()).items():
        for value in values_to_try(parameter, uis[0].get_value(ref_id) or "")[:4]:
            uis.append(_ui(application, {ref_id: value}))
    return uis


def _memory(ui: DynamicUI, seed: int) -> dict[str, bytes]:
    """Random bytes in each code segment of ``ui``."""
    rng = random.Random(seed)
    return {
        segment: bytes(rng.randrange(256) for _ in range(size))
        for segment, size in ui.segment_sizes().items()
    }


def _parameters(application: Application) -> list[str]:
    parameters = application.program.static.parameters
    found: list[str] = ["no-such-parameter"]
    for item in parameters.choice if parameters is not None else []:
        found.extend(member.id for member in getattr(item, "parameter", None) or [item])
    return found


@pytest.mark.parametrize("name", _APPLICATIONS)
def test_a_few_values_decoded_at_their_fields_are_those_of_the_whole_decode(
    name: str,
) -> None:
    application = _application(name)
    parameters = _parameters(application)
    for n, ui in enumerate(_states(application)):
        segments = _memory(ui, n)
        try:
            decoded = ui.decode_memory_parameters(segments)
        except Exception as exc:  # compared, not handled
            with pytest.raises(type(exc)):
                ui.decode_memory_values(segments, parameters)
            continue
        if name == _MEMORY:
            # Told the short way, the union members chosen too.
            short = encode.decode_memory_values(
                ui._app,  # pyright: ignore[reportPrivateUsage]
                ui.indexer,
                segments,
                ui._state,  # pyright: ignore[reportPrivateUsage]
                parameters[1:],
            )
            assert short is not None
            assert short.keys() == decoded.keys()
        for wanted in (parameters, parameters[::3], parameters[1:2]):
            assert ui.decode_memory_values(segments, wanted) == {
                parameter: decoded[parameter]
                for parameter in wanted
                if parameter in decoded
            }


def test_the_short_decode_tells_nothing_with_instances_or_a_field_that_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_instances = _ui(_application("allocator_application.xml"), {})
    with_instances.ui()
    assert with_instances.get_module_instances()
    assert (
        encode.decode_memory_values(
            with_instances._app,  # pyright: ignore[reportPrivateUsage]
            with_instances.indexer,
            _memory(with_instances, 0),
            with_instances._state,  # pyright: ignore[reportPrivateUsage]
            [],
        )
        is None
    )
    application = _application(_MEMORY)
    ui = _ui(application, {})
    ui.ui()
    segments = _memory(ui, 1)
    decoded = ui.decode_memory_parameters(segments)
    parameter = next(iter(decoded))
    original = encode._decode_value  # pyright: ignore[reportPrivateUsage]
    raised = ValueError("no value of its type")

    def raising(raw: int, size_in_bit: int, tc: object, **kwargs: object) -> object:
        if tc is ui.indexer.parameter_types[field.parameter_type].choice:
            raise raised
        return original(raw, size_in_bit, tc, **kwargs)  # type: ignore[arg-type]

    field = encode._memory_fields(  # pyright: ignore[reportPrivateUsage]
        ui._app,  # pyright: ignore[reportPrivateUsage]
        ui.indexer,
    )[parameter]
    monkeypatch.setattr(encode, "_decode_value", raising)
    other = {f"{segment}-copy": data for segment, data in segments.items()} | segments
    assert (
        encode.decode_memory_values(
            ui._app,  # pyright: ignore[reportPrivateUsage]
            ui.indexer,
            other,
            ui._state,  # pyright: ignore[reportPrivateUsage]
            [],
        )
        is None
    )
    # Decoded the whole way then, raising as that does.
    with pytest.raises(ValueError, match="no value of its type"):
        ui.decode_memory_values(other, [parameter])


@pytest.mark.parametrize("name", _APPLICATIONS)
def test_the_scope_of_a_ref_told_by_the_instance_ids_is_the_one_looked_up(
    name: str,
) -> None:
    application = _application(name)
    for ui in _states(application):
        ui.ui()
        refs = [*ui.parameter_ref_ids(), *ui.indexer.parameter_refs, "M-00FA_x"]
        for ref in refs:
            assert ui._locate(ref) == ui._find_scope(ref), ref  # pyright: ignore[reportPrivateUsage]
        scopes: list[ModuleState] = list(ui._state.module_children())  # pyright: ignore[reportPrivateUsage]
        while scopes:
            scope = scopes.pop()
            assert scope.active_param_refs() >= {
                scope.qualify(ref) for ref in scope.local_active_param_refs()
            }
            scopes.extend(scope.module_children())


def test_the_active_refs_of_an_instance_are_qualified_as_each_on_its_own() -> None:
    base = f"{_APP}_MD-1"
    refs = [f"{base}_P-1_R-1", f"{base}_P-2_R-2", f"{_APP}_MD-2_P-3_R-3", "P-4_R-4"]
    # Instances asked again (by the same id and base) and others.
    for instance in (f"{base}_M-1_MI-1", f"{base}_M-1_MI-2", f"{base}_M-1_MI-1"):
        for ref_id in (base, None, f"{base}_SM-1"):
            state = ModuleState(instance, ref_id=ref_id)
            for ref in refs:
                state.mark_active_param(ref)
            assert state.active_param_refs() == {state.qualify(ref) for ref in refs}


def test_the_active_refs_of_a_module_definition_are_those_found_one_by_one() -> None:
    from types import SimpleNamespace
    from typing import Any, cast

    rng = random.Random(7)
    refs = cast(Any, [SimpleNamespace(id=f"R-{n}") for n in range(200)])
    # A list with an id twice is taken one by one.
    twice = cast(Any, [*refs[:50], SimpleNamespace(id="R-3"), *refs[50:]])
    idx = _ui(_application(_MEMORY), {}).indexer
    for md_id, listed in (("MD-1", refs), ("MD-2", twice), ("MD-1", refs)):
        for size in (0, 1, 5, 40, 60, 200):
            active = {f"R-{rng.randrange(260)}" for _ in range(size)}
            found = encode._active_refs_in_order(idx, md_id, listed, active)  # pyright: ignore[reportPrivateUsage]
            assert list(found) == [ref for ref in listed if ref.id in active]


@pytest.mark.parametrize(
    "name",
    sorted(
        path.name
        for path in _FIXTURES.glob("*_application.xml")
        if b"<ModuleDef " in path.read_bytes()
    ),
)
def test_the_active_refs_of_the_application_are_those_of_all_its_active_refs(
    name: str,
) -> None:
    data = (_FIXTURES / name).read_bytes()
    manufacturer = data.split(b'Manufacturer RefId="', 1)[1].split(b'"', 1)[0]
    application = parse_application_xml(data, manufacturer.decode())[0]
    ui = application.dynamic_ui()
    assert ui is not None
    ui.ui()
    state = ui._state  # pyright: ignore[reportPrivateUsage]
    idx = ui.indexer
    refs = idx.parameter_refs

    def application_refs() -> set[str]:
        return encode._application_active_refs(idx, state)  # pyright: ignore[reportPrivateUsage]

    assert application_refs() == {
        ref for ref in state.active_param_refs() if ref in refs
    }
    # An application with a ParameterRef of a module instance's id: that one too.
    qualified = sorted(state.active_param_refs() - state.local_active_param_refs())
    if qualified:
        refs[qualified[0]] = next(iter(refs.values()))
        try:
            assert qualified[0] in application_refs()
            assert application_refs() == {
                ref for ref in state.active_param_refs() if ref in refs
            }
        finally:
            del refs[qualified[0]]


def test_a_value_its_type_cannot_take_raises_at_every_encode() -> None:
    application = _application(_MEMORY)
    ui = _ui(application, {})
    ui.ui()
    write = next(
        w
        for w in encode.collect_writes(
            ui._app,  # pyright: ignore[reportPrivateUsage]
            ui.indexer,
            encode.download_param_values(
                ui._app,  # pyright: ignore[reportPrivateUsage]
                ui.indexer,
                ui._state,  # pyright: ignore[reportPrivateUsage]
            ),
            ui._state,  # pyright: ignore[reportPrivateUsage]
        ).mem
        if isinstance(
            ui.indexer.parameter_types[w.parameter_type].choice,
            encode.ParameterTypeTypeRestriction,
        )
    )
    first = ui.encode_to_memory()
    assert ui.indexer.memory_plans
    ui.indexer.memory_plans.clear()
    assert ui.encode_to_memory() == first
    overrides = {write.param_id: "not-a-value"}
    for _ in range(2):
        with pytest.raises(EncodingError, match=write.param_id):
            encode.encode_to_memory(
                ui._app,  # pyright: ignore[reportPrivateUsage]
                ui.indexer,
                overrides,
            )
