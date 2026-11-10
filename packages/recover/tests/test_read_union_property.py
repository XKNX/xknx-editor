"""A union the application keeps in a property is read from the device, also without a
default member (which a download without a configuration would write).

"Brand" is in property 51 of the object of type 50002; the models of each brand share
property 52, one member per brand, none of them the union's default.
"""

from __future__ import annotations

from typing import Any, cast

from xknxeditor.download.errors import LoadStateError
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.read_config import read_property_values

_APP = "M-00FA_A-0D06-01-0000"
_BRAND = f"{_APP}_P-1_R-1"
_MODEL_A = f"{_APP}_UP-2_R-2"
_MODEL_B = f"{_APP}_UP-3_R-3"


def _enumeration(name: str, count: int) -> str:
    values = "".join(
        f'<Enumeration Text="{name} {n}" Value="{n}" Id="{_APP}_PT-{name}_EN-{n}" />'
        for n in range(count)
    )
    return (
        f'<ParameterType Id="{_APP}_PT-{name}" Name="{name}">'
        f'<TypeRestriction Base="Value" SizeInBit="8">{values}</TypeRestriction>'
        "</ParameterType>"
    )


_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="18" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Union in a property"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<ParameterTypes>
{_enumeration("Brand", 3)}
{_enumeration("A", 4)}
{_enumeration("B", 2)}
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="Brand" ParameterType="{_APP}_PT-Brand" Text="Brand" Value="0">
<Property ObjectType="50002" PropertyId="51" Offset="0" BitOffset="0" /></Parameter>
<Union SizeInBit="8">
<Property ObjectType="50002" PropertyId="52" Offset="0" BitOffset="0" />
<Parameter Id="{_APP}_UP-2" Name="ModelA" ParameterType="{_APP}_PT-A" Text="Model" Value="0" Offset="0" BitOffset="0" />
<Parameter Id="{_APP}_UP-3" Name="ModelB" ParameterType="{_APP}_PT-B" Text="Model" Value="0" Offset="0" BitOffset="0" />
</Union>
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_BRAND}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_MODEL_A}" RefId="{_APP}_UP-2" />
<ParameterRef Id="{_MODEL_B}" RefId="{_APP}_UP-3" />
</ParameterRefs>
</Static>
<Dynamic><ChannelIndependentBlock>
<ParameterBlock Id="{_APP}_PB-1" Text="Inverter">
<ParameterRefRef RefId="{_BRAND}" />
<choose ParamRefId="{_BRAND}">
<when test="1"><ParameterRefRef RefId="{_MODEL_A}" /></when>
<when test="2"><ParameterRefRef RefId="{_MODEL_B}" /></when>
</choose>
</ParameterBlock>
</ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


class _Programmer:
    """A device with one object of type 50002 at index 1 holding ``data`` by property id."""

    def __init__(self, data: dict[int, bytes]) -> None:
        self.data = data
        self.reads: list[int] = []

    def property_chunk_size(self, element_size: int) -> int:
        return 2

    async def locate_object(self, object_type: int, occurrence: int = 0) -> int:
        if object_type != 50002 or occurrence:
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
        self.reads.append(property_id)
        data = self.data.get(property_id, b"") if object_index == 1 else b""
        if start_index == 0:
            return len(data).to_bytes(2, "big")
        return data[start_index - 1 : start_index - 1 + count]


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


async def test_the_union_property_is_read_and_gives_the_model() -> None:
    application = _application()
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_BRAND, "1")
    ui.set_parameter_ref(_MODEL_A, "3")
    written = ui.encode_to_typed_properties()
    assert written == {
        (None, 50002, 51, 0): b"\x01",
        (None, 50002, 52, 0): b"\x03",
    }

    programmer = _Programmer({51: b"\x01", 52: b"\x03"})
    properties = await read_property_values(cast(Any, programmer), application)
    assert properties == written
    # Each property once.
    assert sorted(programmer.reads) == [51, 52]

    recovered = recover_parameters(application, {}, properties)
    assert recovered.values[_BRAND] == "1"
    assert recovered.values[_MODEL_A] == "3"
    assert recovered.complete


async def test_a_device_without_the_union_property_leaves_it_out() -> None:
    properties = await read_property_values(
        cast(Any, _Programmer({51: b"\x02"})), _application()
    )
    assert properties == {(None, 50002, 51, 0): b"\x02"}
