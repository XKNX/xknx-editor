"""Settings a device keeps no copy of are inferred from the group objects its tables link.

A parameter without memory or property (here "Channel A"/"Channel B" and the channel's "Alarms")
only decides which channels, parameters and group objects the application shows. Read back from a
device it stays at its default, so its channel and the channel's settings were lost; with the
linked group objects it takes the value that shows them (issue #207).
"""

from __future__ import annotations

from typing import Any

import pytest

import xknxeditor.recover.recover as recover_mod
from xknxeditor.prod.application import Application, parse_application_xml
from xknxeditor.recover.dossier import DeviceDossier
from xknxeditor.recover.parameters import recover_parameters
from xknxeditor.recover.read_config import RawGroupCommunication
from xknxeditor.recover.tables_decode import DecodedGroupObject, DecodedLink

_APP = "M-00FA_A-0004-01-0000"
_CHANNEL_A = f"{_APP}_P-1_R-1"
_CHANNEL_B = f"{_APP}_P-2_R-2"
_MODE = f"{_APP}_P-3_R-3"
_OPTION = f"{_APP}_P-4_R-4"
_HEATING = f"{_APP}_P-5_R-5"
_COOLING = f"{_APP}_P-6_R-6"
_STATUS = f"{_APP}_P-7_R-7"
_CHANNEL_C = f"{_APP}_P-8_R-8"
_SPEED = f"{_APP}_UP-9_R-9"
_A = f"{_APP}_MD-1_M-1_MI-1_"
_ALARMS_A = f"{_A}P-1_R-1"
_DELAY_A = f"{_A}P-2_R-2"


def _object(object_id: str, number: int, base: str = "") -> str:
    base_number = f' BaseNumber="{base}"' if base else ""
    return (
        f'<ComObject Id="{object_id}" Name="O{number}" Text="Object {number}" Number="{number}"'
        ' FunctionText="" ObjectSize="1 Bit" ReadFlag="Disabled" WriteFlag="Enabled"'
        ' CommunicationFlag="Enabled" TransmitFlag="Enabled" UpdateFlag="Disabled"'
        f' ReadOnInitFlag="Disabled" DatapointType="DPST-1-1"{base_number} />'
    )


def _module(number: int, offset: int, object_base: int) -> str:
    return (
        f'<Module Id="{_APP}_MD-1_M-{number}" RefId="{_APP}_MD-1" Name="Channel {number}">'
        f'<NumericArg RefId="{_APP}_MD-1_A-1" Value="{offset}" />'
        f'<NumericArg RefId="{_APP}_MD-1_A-2" Value="{object_base}" />'
        "</Module>"
    )


_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="4" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Unstored settings"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <Code>
              <AbsoluteSegment Id="{_APP}_AS-0000" Address="0" Size="4"><Data>AAAAAA==</Data></AbsoluteSegment>
            </Code>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-B" Name="B">
                <TypeNumber SizeInBit="1" Type="unsignedInt" minInclusive="0" maxInclusive="1" UIHint="CheckBox" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-S" Name="S">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="1" maxInclusive="10" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-I" Name="I">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="200" maxInclusive="200" />
              </ParameterType>
              <ParameterType Id="{_APP}_PT-N" Name="N">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{_APP}_P-1" Name="ChannelA" ParameterType="{_APP}_PT-B" Text="Channel A" Value="0" />
              <Parameter Id="{_APP}_P-2" Name="ChannelB" ParameterType="{_APP}_PT-B" Text="Channel B" Value="0" />
              <Parameter Id="{_APP}_P-3" Name="Mode" ParameterType="{_APP}_PT-B" Text="Mode" Value="0" />
              <Parameter Id="{_APP}_P-4" Name="Option" ParameterType="{_APP}_PT-B" Text="Option" Value="0" />
              <Parameter Id="{_APP}_P-5" Name="Heating" ParameterType="{_APP}_PT-B" Text="Heating" Value="0" />
              <Parameter Id="{_APP}_P-6" Name="Cooling" ParameterType="{_APP}_PT-B" Text="Cooling" Value="0" />
              <Parameter Id="{_APP}_P-7" Name="Status" ParameterType="{_APP}_PT-B" Text="Status as value" Value="0" />
              <Parameter Id="{_APP}_P-8" Name="ChannelC" ParameterType="{_APP}_PT-B" Text="Channel C" Value="1" />
              <Union SizeInBit="8">
                <Memory CodeSegment="{_APP}_AS-0000" Offset="2" BitOffset="0" />
                <Parameter Id="{_APP}_UP-9" Name="Speed" ParameterType="{_APP}_PT-S" Text="Speed" Value="5" Offset="0" BitOffset="0" />
                <Parameter Id="{_APP}_UP-10" Name="Inactive" ParameterType="{_APP}_PT-I" Text="" Access="None" Value="200" Offset="0" BitOffset="0" />
              </Union>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{_CHANNEL_A}" RefId="{_APP}_P-1" />
              <ParameterRef Id="{_CHANNEL_B}" RefId="{_APP}_P-2" />
              <ParameterRef Id="{_MODE}" RefId="{_APP}_P-3" />
              <ParameterRef Id="{_OPTION}" RefId="{_APP}_P-4" />
              <ParameterRef Id="{_HEATING}" RefId="{_APP}_P-5" />
              <ParameterRef Id="{_COOLING}" RefId="{_APP}_P-6" />
              <ParameterRef Id="{_STATUS}" RefId="{_APP}_P-7" />
              <ParameterRef Id="{_CHANNEL_C}" RefId="{_APP}_P-8" />
              <ParameterRef Id="{_SPEED}" RefId="{_APP}_UP-9" />
              <ParameterRef Id="{_APP}_UP-10_R-10" RefId="{_APP}_UP-10" />
            </ParameterRefs>
            <ComObjectTable>
              {_object(f"{_APP}_O-10", 10)}
              {_object(f"{_APP}_O-11", 11)}
              {_object(f"{_APP}_O-12", 12)}
              {_object(f"{_APP}_O-20", 20)}
              {_object(f"{_APP}_O-21", 21)}
              {_object(f"{_APP}_O-22", 22)}
              {_object(f"{_APP}_O-30", 30)}
            </ComObjectTable>
            <ComObjectRefs>
              <ComObjectRef Id="{_APP}_O-10_R-10" RefId="{_APP}_O-10" />
              <ComObjectRef Id="{_APP}_O-11_R-11" RefId="{_APP}_O-11" />
              <ComObjectRef Id="{_APP}_O-12_R-12" RefId="{_APP}_O-12" />
              <ComObjectRef Id="{_APP}_O-20_R-20" RefId="{_APP}_O-20" />
              <ComObjectRef Id="{_APP}_O-21_R-21" RefId="{_APP}_O-21" />
              <ComObjectRef Id="{_APP}_O-22_R-22" RefId="{_APP}_O-22" />
              <ComObjectRef Id="{_APP}_O-30_R-30" RefId="{_APP}_O-30" />
              <ComObjectRef Id="{_APP}_O-30_R-31" RefId="{_APP}_O-30" ObjectSize="1 Byte" />
            </ComObjectRefs>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_APP}_MD-1" Name="Channel">
              <Arguments>
                <Argument Id="{_APP}_MD-1_A-1" Name="Offset" />
                <Argument Id="{_APP}_MD-1_A-2" Name="ObjectBase" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_APP}_MD-1_P-1" Name="Alarms" ParameterType="{_APP}_PT-B" Text="Alarms" Value="0" />
                  <Parameter Id="{_APP}_MD-1_P-2" Name="Delay" ParameterType="{_APP}_PT-N" Text="Delay" Value="3">
                    <Memory CodeSegment="{_APP}_AS-0000" Offset="0" BitOffset="0" BaseOffset="{_APP}_MD-1_A-1" />
                  </Parameter>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_APP}_MD-1_P-1_R-1" RefId="{_APP}_MD-1_P-1" />
                  <ParameterRef Id="{_APP}_MD-1_P-2_R-2" RefId="{_APP}_MD-1_P-2" />
                </ParameterRefs>
                <ComObjects>
                  {_object(f"{_APP}_MD-1_O-2-1", 1, f"{_APP}_MD-1_A-2")}
                  {_object(f"{_APP}_MD-1_O-2-2", 2, f"{_APP}_MD-1_A-2")}
                </ComObjects>
                <ComObjectRefs>
                  <ComObjectRef Id="{_APP}_MD-1_O-2-1_R-1" RefId="{_APP}_MD-1_O-2-1" />
                  <ComObjectRef Id="{_APP}_MD-1_O-2-2_R-2" RefId="{_APP}_MD-1_O-2-2" />
                </ComObjectRefs>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_APP}_MD-1_PB-1" Text="Channel">
                  <ComObjectRefRef RefId="{_APP}_MD-1_O-2-1_R-1" />
                  <ParameterRefRef RefId="{_APP}_MD-1_P-2_R-2" />
                  <ParameterRefRef RefId="{_APP}_MD-1_P-1_R-1" />
                  <choose ParamRefId="{_APP}_MD-1_P-1_R-1">
                    <when test="1"><ComObjectRefRef RefId="{_APP}_MD-1_O-2-2_R-2" /></when>
                  </choose>
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <ParameterBlock Id="{_APP}_PB-1" Text="General">
                <ParameterRefRef RefId="{_CHANNEL_A}" />
                <ParameterRefRef RefId="{_CHANNEL_B}" />
                <ParameterRefRef RefId="{_MODE}" />
                <choose ParamRefId="{_MODE}">
                  <when test="0">
                    <ParameterRefRef RefId="{_OPTION}" />
                    <choose ParamRefId="{_OPTION}">
                      <when test="1">
                        <ComObjectRefRef RefId="{_APP}_O-11_R-11" />
                        <ComObjectRefRef RefId="{_APP}_O-12_R-12" />
                      </when>
                    </choose>
                  </when>
                  <when test="1"><ComObjectRefRef RefId="{_APP}_O-10_R-10" /></when>
                </choose>
                <ParameterRefRef RefId="{_HEATING}" />
                <choose ParamRefId="{_HEATING}">
                  <when test="1">
                    <ComObjectRefRef RefId="{_APP}_O-20_R-20" />
                    <ComObjectRefRef RefId="{_APP}_O-21_R-21" />
                  </when>
                </choose>
                <ParameterRefRef RefId="{_COOLING}" />
                <choose ParamRefId="{_COOLING}">
                  <when test="1">
                    <ComObjectRefRef RefId="{_APP}_O-20_R-20" />
                    <ComObjectRefRef RefId="{_APP}_O-22_R-22" />
                  </when>
                </choose>
                <ParameterRefRef RefId="{_STATUS}" />
                <choose ParamRefId="{_STATUS}">
                  <when test="0"><ComObjectRefRef RefId="{_APP}_O-30_R-30" /></when>
                  <when test="1"><ComObjectRefRef RefId="{_APP}_O-30_R-31" /></when>
                </choose>
                <ParameterRefRef RefId="{_CHANNEL_C}" />
                <choose ParamRefId="{_CHANNEL_C}">
                  <when test="0"><ParameterRefRef RefId="{_APP}_UP-10_R-10" /></when>
                  <when test="1"><ParameterRefRef RefId="{_SPEED}" /></when>
                </choose>
              </ParameterBlock>
            </ChannelIndependentBlock>
            <choose ParamRefId="{_CHANNEL_A}"><when test="1">{_module(1, 0, 0)}</when></choose>
            <choose ParamRefId="{_CHANNEL_B}"><when test="1">{_module(2, 1, 2)}</when></choose>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


@pytest.fixture(scope="module")
def application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _device(application: Application, **values: str) -> dict[str, bytes]:
    """The memory of a device configured with ``values`` (ref id -> value)."""
    ui = application.dynamic_ui()
    assert ui is not None
    for ref_id, value in values.items():
        ui.set_parameter_ref(ref_id, value)
    return ui.encode_to_memory()


def test_object_selectors(application: Application) -> None:
    ui = application.dynamic_ui()
    assert ui is not None
    # Settings on the way to a group object or module, and "Mode", which shows "Option".
    assert ui.object_param_refs == {
        _CHANNEL_A,
        _CHANNEL_B,
        _MODE,
        _OPTION,
        _HEATING,
        _COOLING,
        _STATUS,
        f"{_APP}_MD-1_P-1_R-1",
    }


def test_without_links_a_channel_stays_off(application: Application) -> None:
    segments = _device(application, **{_CHANNEL_A: "1", _DELAY_A: "7"})
    recovered = recover_parameters(application, segments)
    assert recovered.values.get(_CHANNEL_A, "0") == "0"
    assert _DELAY_A not in recovered.values


def test_linked_objects_switch_a_channel_and_its_settings_on(
    application: Application,
) -> None:
    segments = _device(application, **{_CHANNEL_A: "1", _DELAY_A: "7"})
    recovered = recover_parameters(application, segments, linked_objects={1, 2})
    assert recovered.values[_CHANNEL_A] == "1"
    # The channel's object 2 needs its unstored "Alarms" setting on too.
    assert recovered.values[_ALARMS_A] == "1"
    # Shown now, the channel's stored setting is read from its memory.
    assert recovered.values[_DELAY_A] == "7"
    # Channel B has no linked object and stays off.
    assert recovered.values.get(_CHANNEL_B, "0") == "0"


def test_a_channel_is_found_by_its_own_objects(application: Application) -> None:
    # Channel B's objects are 3 and 4 (object base 2); its delay sits at offset 1.
    segments = _device(
        application, **{_CHANNEL_B: "1", f"{_APP}_MD-1_M-2_MI-1_P-2_R-2": "9"}
    )
    recovered = recover_parameters(application, segments, linked_objects={3})
    assert recovered.values[_CHANNEL_B] == "1"
    assert recovered.values.get(_CHANNEL_A, "0") == "0"
    assert recovered.values[f"{_APP}_MD-1_M-2_MI-1_P-2_R-2"] == "9"
    assert recovered.values.get(f"{_APP}_MD-1_M-2_MI-1_P-1_R-1", "0") == "0"


def test_settings_that_hide_each_others_objects_are_not_combined(
    application: Application,
) -> None:
    # "Mode" 1 shows object 10, "Option" 1 (only under mode 0) objects 11 and 12: together
    # they show only object 10, so the one that shows more is taken.
    segments = _device(application, **{_OPTION: "1"})
    recovered = recover_parameters(application, segments, linked_objects={10, 11, 12})
    assert recovered.values[_OPTION] == "1"
    assert recovered.values.get(_MODE, "0") == "0"


def test_settings_that_show_the_same_object_are_not_all_taken(
    application: Application,
) -> None:
    # "Heating" and "Cooling" both show the valve (object 20); only cooling's own object 22
    # is linked.
    segments = _device(application, **{_COOLING: "1"})
    recovered = recover_parameters(application, segments, linked_objects={20, 22})
    assert recovered.values[_COOLING] == "1"
    assert recovered.values.get(_HEATING, "0") == "0"


def test_the_object_size_picks_the_datapoint_type(application: Application) -> None:
    # Object 30 is shown either way; the device's table says it is one byte.
    segments = _device(application, **{_STATUS: "1"})
    plain = recover_parameters(application, segments, linked_objects={30})
    assert plain.values.get(_STATUS, "0") == "0"
    sized = recover_parameters(
        application, segments, linked_objects={30}, object_sizes={30: "1 Byte"}
    )
    assert sized.values[_STATUS] == "1"


def test_the_memory_tells_an_inactive_channel(application: Application) -> None:
    # Inactive, channel C writes 200 where an active one keeps its speed (1..10): read as a
    # speed the bytes are no value, so the channel is taken as inactive.
    segments = _device(application, **{_CHANNEL_C: "0"})
    recovered = recover_parameters(application, segments)
    assert recovered.values[_CHANNEL_C] == "0"
    assert _SPEED not in recovered.values


def test_the_memory_keeps_an_active_channel(application: Application) -> None:
    segments = _device(application, **{_SPEED: "7"})
    recovered = recover_parameters(application, segments)
    assert recovered.values.get(_CHANNEL_C, "1") == "1"
    assert recovered.values[_SPEED] == "7"


def test_a_setting_that_shows_no_linked_object_more_is_left(
    application: Application,
) -> None:
    segments = _device(application)
    plain = recover_parameters(application, segments)
    # Object 99 does not exist: no setting can show it.
    assert recover_parameters(application, segments, linked_objects={99}) == plain


async def test_recover_device_links_the_channel_objects(
    application: Application, monkeypatch: pytest.MonkeyPatch
) -> None:
    segments = _device(application, **{_CHANNEL_A: "1", _ALARMS_A: "1", _STATUS: "1"})
    links = [
        DecodedLink(group_address=0x0801, group_object_number=2, sending=True),
        DecodedLink(group_address=0x0802, group_object_number=30, sending=True),
    ]
    status = DecodedGroupObject(
        number=30,
        priority="Low",
        communication=True,
        read=False,
        write=True,
        transmit=True,
        update=False,
        read_on_init=False,
        size_code=7,
        object_size="1 Byte",
    )

    async def group_communication(*_args: Any) -> RawGroupCommunication:
        return RawGroupCommunication(
            device_address=None,
            group_addresses=[0x0801, 0x0802],
            links=links,
            group_objects={30: status},
        )

    async def memory(*_args: Any) -> dict[str, bytes]:
        return segments

    async def properties(*_args: Any) -> dict[Any, bytes]:
        return {}

    async def dossier(*_args: Any) -> DeviceDossier:
        return DeviceDossier()

    class Programmer:
        async def read_device_descriptor(self) -> int:
            return 0x07B0

    monkeypatch.setattr(recover_mod, "read_group_communication", group_communication)
    monkeypatch.setattr(recover_mod, "read_parameter_memory", memory)
    monkeypatch.setattr(recover_mod, "read_property_values", properties)
    monkeypatch.setattr(recover_mod, "read_dossier", dossier)
    recovered = await recover_mod.recover_device(
        Programmer(),  # type: ignore[arg-type]
        application,
        address="1.1.20",
    )
    assert recovered.parameters.values[_ALARMS_A] == "1"
    # The table's object size picks the datapoint type of object 30.
    assert recovered.parameters.values[_STATUS] == "1"
    assert recovered.com_object_refs is not None
    assert recovered.com_object_refs[30] == f"{_APP}_O-30_R-31"
    # The linked number resolves to the object of the channel's instance, which the project
    # creates, so the link is not dropped.
    assert recovered.com_object_refs[2] == f"{_A}O-2-2_R-2"
