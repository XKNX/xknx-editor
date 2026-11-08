"""Parameters in an array property are read back from every element they take.

The download writes a property of one octet per element as a run of elements from the first
on (``LdCtrlWriteProp Count="80"``); a parameter at offset 4 is in the fifth element, which a
read of the first element alone does not give back.
"""

from __future__ import annotations

from typing import Any, cast

from xknx.exceptions import ManagementConnectionError

from xknxeditor.download.errors import VerificationError
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.read_config import read_property_values

_APP = "M-00FA_A-0D02-01-0000"
_FIRST = f"{_APP}_P-1_R-1"
_FIFTH = f"{_APP}_P-2_R-2"
_XML = f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{_APP}" ApplicationNumber="14" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Property elements"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<ParameterTypes>
<ParameterType Id="{_APP}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{_APP}_P-1" Name="First" ParameterType="{_APP}_PT-N" Text="First" Value="0">
<Property ObjectIndex="6" PropertyId="23" Offset="0" BitOffset="0" /></Parameter>
<Parameter Id="{_APP}_P-2" Name="Fifth" ParameterType="{_APP}_PT-N" Text="Fifth" Value="0">
<Property ObjectIndex="6" PropertyId="23" Offset="4" BitOffset="0" /></Parameter>
</Parameters>
<ParameterRefs>
<ParameterRef Id="{_FIRST}" RefId="{_APP}_P-1" />
<ParameterRef Id="{_FIFTH}" RefId="{_APP}_P-2" />
</ParameterRefs>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{_APP}_PB-1" Text="Settings">
<ParameterRefRef RefId="{_FIRST}" /><ParameterRefRef RefId="{_FIFTH}" />
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>"""


class _Programmer:
    """A device holding PID 23 of object 6 as elements of one octet (element 0: how many),
    answering ``frame`` elements per read at most; ``failing`` elements after the first cannot
    be read."""

    def __init__(
        self, data: bytes, *, frame: int = 2, failing: Exception | None = None
    ) -> None:
        self.data = data
        self.frame = frame
        self.failing = failing
        self.reads: list[tuple[int, int]] = []

    def property_chunk_size(self, element_size: int) -> int:
        assert element_size == 1
        return self.frame

    async def read_property(
        self,
        object_index: int,
        property_id: int,
        *,
        count: int = 1,
        start_index: int = 1,
    ) -> bytes:
        assert (object_index, property_id) == (6, 23)
        assert count <= self.frame
        self.reads.append((start_index, count))
        if start_index == 0:
            return len(self.data).to_bytes(2, "big")
        if start_index > 1 and self.failing is not None:
            raise self.failing
        return self.data[start_index - 1 : start_index - 1 + count]


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application) -> bytes:
    ui = application.dynamic_ui()
    assert ui is not None
    ui.set_parameter_ref(_FIRST, "7")
    ui.set_parameter_ref(_FIFTH, "42")
    data = next(iter(ui.encode_to_properties().values()))
    assert data[0] == 7
    assert data[4] == 42
    return data.ljust(80, b"\x00")


async def test_every_element_the_parameters_take_is_read() -> None:
    application = _application()
    programmer = _Programmer(_device(application))
    properties = await read_property_values(cast(Any, programmer), application)
    assert properties == {(6, 23, 0): bytes([7, 0, 0, 0, 42])}
    # The first element, how many the device holds, then the rest in frames of two.
    assert programmer.reads == [(1, 1), (0, 1), (2, 2), (4, 2)]
    recovered = recover_parameters(application, {}, properties)
    assert recovered.values[_FIRST] == "7"
    assert recovered.values[_FIFTH] == "42"


async def test_no_element_is_read_past_those_the_device_holds() -> None:
    # A two-octet parameter in a property the device keeps as one element of one octet: the
    # second octet is nowhere, the parameter is not read (no zeros made up for it).
    application = _application()
    programmer = _Programmer(_device(application)[:3])
    properties = await read_property_values(cast(Any, programmer), application)
    assert properties == {(6, 23, 0): bytes([7, 0, 0])}
    assert programmer.reads == [(1, 1), (0, 1), (2, 2)]
    recovered = recover_parameters(application, {}, properties)
    assert recovered.values[_FIRST] == "7"
    assert _FIFTH not in recovered.values


async def test_elements_that_cannot_be_read_are_left_out() -> None:
    application = _application()
    programmer = _Programmer(
        _device(application), failing=VerificationError("no such element")
    )
    properties = await read_property_values(cast(Any, programmer), application)
    assert properties == {(6, 23, 0): bytes([7])}


async def test_a_disconnect_while_reading_more_elements_propagates() -> None:
    application = _application()
    programmer = _Programmer(
        _device(application), failing=ManagementConnectionError("disconnected")
    )
    try:
        await read_property_values(cast(Any, programmer), application)
    except ManagementConnectionError:
        return
    raise AssertionError("the disconnect did not propagate")
