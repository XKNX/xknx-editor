"""Many datapoints whose types the device keeps no copy of, told by hidden markers.

Each datapoint's type (a setting the device keeps no copy of) picks one of the references of
a hidden marker the device stores, each with its own fixed value, and an object of its own
that no group address is linked to. The memory holds the marker of every datapoint: it names
each type at once, more of them than a search that tries one type after the other gets to.
The marker of each type can also be a member of a union of its own (one byte for all of a
datapoint's types). Two types with the same marker (one of them the choose's default branch) are told apart by the
size the device's table gives their object.
"""

from __future__ import annotations

import base64

import pytest

from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import RecoveryEffort, recover_parameters

_APP = "M-00FA_A-0D02-01-0000"
_SEGMENT = f"{_APP}_AS-0000"
_COUNT = 70
# Per type: the value of its marker and the size of its object (none for type 0).
_MARKERS = {0: 0, 1: 1, 2: 7, 3: 7}
_SIZES = {1: "1 Bit", 2: "1 Bit", 3: "1 Byte"}
_LAST = 3


def _type(channel: int) -> str:
    return f"{_APP}_P-{channel}_R-{channel}"


def _marker(channel: int, kind: int, *, union: bool) -> str:
    """The reference of the marker of ``channel`` its type ``kind`` holds: of the channel's
    marker, or (``union``) of the member of the channel's union for that type."""
    marker = 1000 + channel
    if union:
        return f"{_APP}_UP-{marker * 10 + kind}_R-{marker * 10 + kind}"
    return f"{_APP}_P-{marker}_R-{marker * 10 + kind}"


def _xml(*, union: bool) -> str:
    """The application; with ``union`` each type but the first has a union member of its own
    as its marker (all of them in the same byte), the first none."""
    parameters: list[str] = []
    refs: list[str] = []
    objects: list[str] = []
    object_refs: list[str] = []
    dynamic: list[str] = []
    for channel in range(1, _COUNT + 1):
        marker = 1000 + channel
        memory = (
            f'<Memory CodeSegment="{_SEGMENT}" Offset="{channel - 1}" BitOffset="0" />'
        )
        parameters.append(
            f'<Parameter Id="{_APP}_P-{channel}" Name="Type{channel}" '
            f'ParameterType="{_APP}_PT-Type" Text="Type {channel}" Value="0" />'
        )
        refs.append(
            f'<ParameterRef Id="{_type(channel)}" RefId="{_APP}_P-{channel}" />'
        )
        if union:
            members = [kind for kind in _MARKERS if kind]
            parameters.append(
                f'<Union SizeInBit="8">{memory}'
                + "".join(
                    f'<Parameter Id="{_APP}_UP-{marker * 10 + kind}" Name="M{kind}" '
                    f'ParameterType="{_APP}_PT-N" Offset="0" BitOffset="0" Text="" '
                    f'Access="None" Value="{_MARKERS[kind]}" />'
                    for kind in members
                )
                + "</Union>"
            )
            refs.extend(
                f'<ParameterRef Id="{_marker(channel, kind, union=True)}" '
                f'RefId="{_APP}_UP-{marker * 10 + kind}" />'
                for kind in members
            )
        else:
            parameters.append(
                f'<Parameter Id="{_APP}_P-{marker}" Name="Marker{channel}" '
                f'ParameterType="{_APP}_PT-N" Text="" Access="None" Value="0">'
                f"{memory}</Parameter>"
            )
            refs.extend(
                f'<ParameterRef Id="{_marker(channel, kind, union=False)}" '
                f'RefId="{_APP}_P-{marker}" Value="{value}" />'
                for kind, value in _MARKERS.items()
            )
        objects.append(
            f'<ComObject Id="{_APP}_O-{channel}" Name="O{channel}" Text="Object {channel}" '
            f'Number="{channel}" FunctionText="" ObjectSize="1 Bit" ReadFlag="Disabled" '
            'WriteFlag="Enabled" CommunicationFlag="Enabled" TransmitFlag="Enabled" '
            'UpdateFlag="Disabled" ReadOnInitFlag="Disabled" />'
        )
        object_refs.extend(
            f'<ComObjectRef Id="{_APP}_O-{channel}_R-{channel * 10 + kind}" '
            f'RefId="{_APP}_O-{channel}" ObjectSize="{size}" />'
            for kind, size in _SIZES.items()
        )
        # The last type is the choose's default branch.
        whens = "".join(
            ('<when default="true">' if kind == _LAST else f'<when test="{kind}">')
            + (
                f'<ParameterRefRef RefId="{_marker(channel, kind, union=union)}" />'
                if kind or not union
                else ""
            )
            + (
                f'<ComObjectRefRef RefId="{_APP}_O-{channel}_R-{channel * 10 + kind}" />'
                if kind
                else ""
            )
            + "</when>"
            for kind in _MARKERS
        )
        dynamic.append(
            f'<ParameterRefRef RefId="{_type(channel)}" />'
            f'<choose ParamRefId="{_type(channel)}">{whens}</choose>'
        )
    data = base64.b64encode(bytes(_COUNT)).decode()
    return f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="14" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Datapoint types"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{_SEGMENT}" Address="0" Size="{_COUNT}"><Data>{data}</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-Type" Name="Type"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Off" Value="0" Id="{_APP}_PT-Type_EN-0" />
<Enumeration Text="Switch" Value="1" Id="{_APP}_PT-Type_EN-1" />
<Enumeration Text="Alarm" Value="2" Id="{_APP}_PT-Type_EN-2" />
<Enumeration Text="Count" Value="3" Id="{_APP}_PT-Type_EN-3" />
</TypeRestriction></ParameterType>
<ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
</ParameterTypes>
<Parameters>{"".join(parameters)}</Parameters>
<ParameterRefs>{"".join(refs)}</ParameterRefs>
<ComObjectTable>{"".join(objects)}</ComObjectTable>
<ComObjectRefs>{"".join(object_refs)}</ComObjectRefs>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="Datapoints">
{"".join(dynamic)}
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


def _application(*, union: bool = False) -> Application:
    return parse_application_xml(_xml(union=union).encode(), "M-00FA")[0]


def _device(application: Application, types: dict[int, int]) -> dict[str, bytes]:
    """The memory of a device whose datapoints have the ``types`` (channel -> type)."""
    ui = application.dynamic_ui()
    assert ui is not None
    for channel, kind in types.items():
        ui.set_parameter_ref(_type(channel), str(kind))
    return ui.encode_to_memory()


def _recovered_types(
    application: Application, types: dict[int, int], sizes: dict[int, str]
) -> tuple[bool, dict[int, str]]:
    recovered = recover_parameters(
        application,
        _device(application, types),
        object_sizes=sizes,
        effort=RecoveryEffort.FAST,
    )
    return recovered.complete, {
        channel: recovered.values.get(_type(channel), "0")
        for channel in range(1, _COUNT + 1)
    }


@pytest.mark.parametrize("union", [False, True])
def test_the_markers_name_the_type_of_every_datapoint(union: bool) -> None:
    application = _application(union=union)
    types = {channel: 1 for channel in range(1, _COUNT + 1)}
    complete, recovered = _recovered_types(
        application, types, dict.fromkeys(types, "1 Bit")
    )
    assert complete
    assert recovered == {channel: "1" for channel in types}


@pytest.mark.parametrize("union", [False, True])
def test_the_table_tells_apart_types_with_the_same_marker(union: bool) -> None:
    application = _application(union=union)
    types = {channel: 2 + channel % 2 for channel in range(1, _COUNT + 1)}
    sizes = {channel: _SIZES[kind] for channel, kind in types.items()}
    complete, recovered = _recovered_types(application, types, sizes)
    assert complete
    assert recovered == {channel: str(kind) for channel, kind in types.items()}


@pytest.mark.parametrize("union", [False, True])
def test_datapoints_whose_marker_fits_keep_their_type(union: bool) -> None:
    # Every other datapoint is off: its marker holds the value of the default type (or, with
    # a union, the initial value of the byte).
    application = _application(union=union)
    types = {channel: channel % 2 for channel in range(1, _COUNT + 1)}
    sizes = {channel: "1 Bit" for channel, kind in types.items() if kind}
    complete, recovered = _recovered_types(application, types, sizes)
    assert complete
    assert recovered == {channel: str(kind) for channel, kind in types.items()}
