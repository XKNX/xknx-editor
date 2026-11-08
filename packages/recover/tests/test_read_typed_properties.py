"""Parameters in properties of objects addressed by type are read from the device's object of
that type and occurrence, and kept apart per type.

The application keeps a count in property 54 of its object of type 60001; each module
instance keeps a setting in property 54 of its own object of type 50001 (its occurrence an
argument of the instance). Keyed by object index alone, the three would be one property.
"""

from __future__ import annotations

from typing import Any, cast

from xknx.exceptions import ManagementConnectionError

from xknxeditor.download.errors import LoadStateError
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.read_config import read_property_values

_APP = "M-00FA_A-0D03-01-0000"
_COUNT = f"{_APP}_P-1_R-1"
_FIRST = f"{_APP}_MD-1_M-1_MI-1_P-1_R-1"
_SECOND = f"{_APP}_MD-1_M-2_MI-1_P-1_R-1"
_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="15" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Typed properties"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Count" ParameterType="{_APP}_PT-N" Text="Count" Value="0">
<Property ObjectType="60001" PropertyId="54" Offset="0" BitOffset="0" /></Parameter>
</Parameters>
<ParameterRefs><ParameterRef Id="{_COUNT}" RefId="{_APP}_P-1" /></ParameterRefs>
</Static>
<ModuleDefs>
<ModuleDef Id="{_APP}_MD-1" Name="Notification">
<Arguments><Argument Id="{_APP}_MD-1_A-1" Name="Index" /></Arguments>
<Static>
<Parameters>
<Parameter Id="{_APP}_MD-1_P-1" Name="Priority" ParameterType="{_APP}_PT-N" Text="Priority" Value="0">
<Property ObjectType="50001" PropertyId="54" Offset="0" BitOffset="0" BaseOccurrence="{_APP}_MD-1_A-1" /></Parameter>
</Parameters>
<ParameterRefs><ParameterRef Id="{_APP}_MD-1_P-1_R-1" RefId="{_APP}_MD-1_P-1" /></ParameterRefs>
</Static>
<Dynamic><ParameterBlock Id="{_APP}_MD-1_PB-1" Text="Notification">
<ParameterRefRef RefId="{_APP}_MD-1_P-1_R-1" />
</ParameterBlock></Dynamic>
</ModuleDef>
</ModuleDefs>
<Dynamic><ChannelIndependentBlock>
<ParameterBlock Id="{_APP}_PB-1" Text="Settings"><ParameterRefRef RefId="{_COUNT}" /></ParameterBlock>
<Module Id="{_APP}_MD-1_M-1" RefId="{_APP}_MD-1"><NumericArg RefId="{_APP}_MD-1_A-1" Value="0" /></Module>
<Module Id="{_APP}_MD-1_M-2" RefId="{_APP}_MD-1"><NumericArg RefId="{_APP}_MD-1_A-1" Value="1" /></Module>
</ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


class _Programmer:
    """A device with the objects ``types`` (index -> type), holding the properties ``data``
    ((index, property id) -> one element of one octet)."""

    def __init__(
        self, types: dict[int, int], data: dict[tuple[int, int], bytes]
    ) -> None:
        self.types = types
        self.data = data

    def property_chunk_size(self, element_size: int) -> int:
        return 1

    async def locate_object(self, object_type: int, occurrence: int = 0) -> int:
        found = [
            index for index, kind in sorted(self.types.items()) if kind == object_type
        ]
        if occurrence >= len(found):
            raise LoadStateError(f"no object of type {object_type} #{occurrence}")
        return found[occurrence]

    async def read_property(
        self,
        object_index: int,
        property_id: int,
        *,
        count: int = 1,
        start_index: int = 1,
    ) -> bytes:
        data = self.data.get((object_index, property_id), b"")
        if start_index == 0:
            return len(data).to_bytes(2, "big")
        return data[start_index - 1 : start_index - 1 + count]


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device() -> _Programmer:
    return _Programmer(
        {0: 0, 1: 50001, 2: 60001, 3: 50001},
        {(2, 54): b"\x10", (1, 54): b"\x03", (3, 54): b"\x04"},
    )


def test_the_encoding_keys_the_objects_apart() -> None:
    ui = _application().dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_COUNT, "16")
    ui.set_parameter_ref(_FIRST, "3")
    ui.set_parameter_ref(_SECOND, "4")
    assert ui.encode_to_typed_properties() == {
        (None, 60001, 54, 0): b"\x10",
        (None, 50001, 54, 0): b"\x03",
        (None, 50001, 54, 1): b"\x04",
    }


async def test_each_object_by_type_is_read_and_decoded_on_its_own() -> None:
    application = _application()
    properties = await read_property_values(cast(Any, _device()), application)
    assert properties == {
        (None, 60001, 54, 0): b"\x10",
        (None, 50001, 54, 0): b"\x03",
        (None, 50001, 54, 1): b"\x04",
    }
    recovered = recover_parameters(application, {}, properties)
    assert recovered.values[_COUNT] == "16"
    assert recovered.values[_FIRST] == "3"
    assert recovered.values[_SECOND] == "4"


async def test_an_object_the_device_does_not_have_is_left_out() -> None:
    # No object of type 60001, one of type 50001.
    programmer = _Programmer({0: 0, 1: 50001}, {(1, 54): b"\x03"})
    properties = await read_property_values(cast(Any, programmer), _application())
    assert properties == {(None, 50001, 54, 0): b"\x03"}


class _Disconnected(_Programmer):
    async def locate_object(self, object_type: int, occurrence: int = 0) -> int:
        raise ManagementConnectionError("disconnected")


async def test_a_disconnect_while_locating_an_object_propagates() -> None:
    programmer = _Disconnected({0: 0}, {})
    try:
        await read_property_values(cast(Any, programmer), _application())
    except ManagementConnectionError:
        return
    raise AssertionError("the disconnect did not propagate")
