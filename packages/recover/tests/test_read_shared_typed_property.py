"""A property of an object addressed by type that the application and its module instances
share: the instances keep their parameters at offsets of their own (a BaseOffset) after the
application's, and the read takes the property as far as theirs go."""

from __future__ import annotations

from typing import Any, cast

from xknxeditor.download.errors import LoadStateError
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.read_config import read_property_values

_APP = "M-00FA_A-0D04-01-0000"
_COUNT = f"{_APP}_P-1_R-1"
_FIRST = f"{_APP}_MD-1_M-1_MI-1_P-1_R-1"
_SECOND = f"{_APP}_MD-1_M-2_MI-1_P-1_R-1"
_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="16" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Shared typed property"
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
<ModuleDef Id="{_APP}_MD-1" Name="Channel">
<Arguments><Argument Id="{_APP}_MD-1_A-1" Name="Offset" /></Arguments>
<Static>
<Parameters>
<Parameter Id="{_APP}_MD-1_P-1" Name="Mode" ParameterType="{_APP}_PT-N" Text="Mode" Value="0">
<Property ObjectType="60001" PropertyId="54" Offset="1" BitOffset="0" BaseOffset="{_APP}_MD-1_A-1" /></Parameter>
</Parameters>
<ParameterRefs><ParameterRef Id="{_APP}_MD-1_P-1_R-1" RefId="{_APP}_MD-1_P-1" /></ParameterRefs>
</Static>
<Dynamic><ParameterBlock Id="{_APP}_MD-1_PB-1" Text="Channel">
<ParameterRefRef RefId="{_APP}_MD-1_P-1_R-1" />
</ParameterBlock></Dynamic>
</ModuleDef>
</ModuleDefs>
<Dynamic><ChannelIndependentBlock>
<ParameterBlock Id="{_APP}_PB-1" Text="Settings"><ParameterRefRef RefId="{_COUNT}" /></ParameterBlock>
<Module Id="{_APP}_MD-1_M-1" RefId="{_APP}_MD-1"><NumericArg RefId="{_APP}_MD-1_A-1" Value="3" /></Module>
<Module Id="{_APP}_MD-1_M-2" RefId="{_APP}_MD-1"><NumericArg RefId="{_APP}_MD-1_A-1" Value="5" /></Module>
</ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


class _Programmer:
    """A device with one object of type 60001 at index 1, its property 54 holding ``data``
    (one octet per element)."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.reads: list[tuple[int, int]] = []

    def property_chunk_size(self, element_size: int) -> int:
        return 2

    async def locate_object(self, object_type: int, occurrence: int = 0) -> int:
        if object_type != 60001 or occurrence:
            raise LoadStateError(f"no object of type {object_type} #{occurrence}")
        return 1

    async def read_property(
        self,
        object_index: int,
        property_id: int,
        *,
        count: int = 1,
        start_index: int = 1,
    ) -> bytes:
        self.reads.append((start_index, count))
        if (object_index, property_id) != (1, 54):
            return b""
        if start_index == 0:
            return len(self.data).to_bytes(2, "big")
        return self.data[start_index - 1 : start_index - 1 + count]


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def test_the_instances_keep_their_settings_after_the_applications() -> None:
    ui = _application().dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_COUNT, "16")
    ui.set_parameter_ref(_FIRST, "3")
    ui.set_parameter_ref(_SECOND, "4")
    assert ui.encode_to_typed_properties() == {
        (None, 60001, 54, 0): bytes.fromhex("10000000030004"),
    }


async def test_a_shared_property_is_read_as_far_as_the_instances_keep_settings() -> (
    None
):
    application = _application()
    properties = await read_property_values(
        cast(Any, _Programmer(bytes.fromhex("10000000030004"))), application
    )
    assert properties == {(None, 60001, 54, 0): bytes.fromhex("10000000030004")}
    recovered = recover_parameters(application, {}, properties)
    assert recovered.values[_COUNT] == "16"
    assert recovered.values[_FIRST] == "3"
    assert recovered.values[_SECOND] == "4"
