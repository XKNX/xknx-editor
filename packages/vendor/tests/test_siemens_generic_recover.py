"""Reading a Generic Plug-in configuration back from the parameter memory a device holds.

The description is a small, made-up document in the plug-in's XML dialect: no manufacturer
product data is used.
"""

import asyncio

import pytest

from xknxeditor.vendor.siemens_generic import parse_description, parse_settings
from xknxeditor.vendor.siemens_generic.configuration import (
    default_config,
    edit,
    evaluate,
)
from xknxeditor.vendor.siemens_generic.description import Description
from xknxeditor.vendor.siemens_generic.image import compile_image
from xknxeditor.vendor.siemens_generic.recover import (
    DeviceImage,
    decode_device,
    image_of,
    memory_range,
    read_device_image,
)

_BASE = 0x4000

DESCRIPTION = f"""<ApplicationProgram Id="id-DEMO_0002_01"
 xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
 xsi:noNamespaceSchemaLocation="GenericPlugIn_4.xsd"><Static>
 <CodeSegments><CodeSegment Id="s" Address="{_BASE}" AllocLength="32"/></CodeSegments>
 <ParameterTypes>
  <ParameterType Id="t_mode"><TypeRestriction Size="8">
   <Enumeration Id="e_off" Name="dm_Off" Text="Off" Value="0"/>
   <Enumeration Id="e_a" Name="dm_A" Text="Mode A" Value="1"/>
   <Enumeration Id="e_b" Name="dm_B" Text="Mode B" Value="2"/>
  </TypeRestriction></ParameterType>
  <ParameterType Id="t_yn"><TypeRestriction Size="8">
   <Enumeration Id="e_no" Name="yn_No" Text="No" Value="0"/>
   <Enumeration Id="e_yes" Name="yn_Yes" Text="Yes" Value="1"/>
  </TypeRestriction></ParameterType>
  <ParameterType Id="t_word"><TypeNumber Size="16" IntelOrderSwap="true"
   minInclusive="0" maxInclusive="1000"/></ParameterType>
  <ParameterType Id="t_sbyte"><TypeNumber Size="8" Type="signed"/></ParameterType>
  <ParameterType Id="t_text"><TypeText Size="32"/></ParameterType>
  <ParameterType Id="t_order"><TypeText Size="64"/></ParameterType>
 </ParameterTypes>
 <Parameters>
  <Parameter Id="p_mode" RefId="t_mode" Value="dm_Off"><Memory CodeSegment="s" Offset="0"/></Parameter>
  <Parameter Id="p_delay" RefId="t_word" Value="300"><Memory CodeSegment="s" Offset="1"/></Parameter>
  <Parameter Id="p_name" RefId="t_text" Value="Lamp"><Memory CodeSegment="s" Offset="3"/></Parameter>
  <Parameter Id="p_fb" RefId="t_yn" Value="0"><Memory CodeSegment="s" Offset="7"/></Parameter>
  <Parameter Id="p_derived" RefId="t_yn" Value="0"><Memory CodeSegment="s" Offset="8"/></Parameter>
  <Parameter Id="p_offset" RefId="t_sbyte" Value="0"><Memory CodeSegment="s" Offset="9"/></Parameter>
  <Parameter Id="p_extra" RefId="t_sbyte" Value="5"><Memory CodeSegment="s" Offset="10"/></Parameter>
  <Parameter Id="p_note" RefId="t_word" Value="7"/>
  <Parameter Id="p_order" RefId="t_order" Value=""/>
 </Parameters>
 <ParameterRefs>
  <ParameterRef Id="r_mode" RefId="p_mode"/>
  <ParameterRef Id="r_delay" RefId="p_delay"/>
  <ParameterRef Id="r_name" RefId="p_name"/>
  <ParameterRef Id="r_fb" RefId="p_fb"/>
  <ParameterRef Id="r_derived" RefId="p_derived"/>
  <ParameterRef Id="r_offset" RefId="p_offset"/>
  <ParameterRef Id="r_extra" RefId="p_extra"/>
  <ParameterRef Id="r_note" RefId="p_note"/>
  <ParameterRef Id="r_order" RefId="p_order" GlobalVariableReference="DeviceOrderNumber"/>
 </ParameterRefs></Static>
 <Dynamic>
  <ChannelIndependentBlock><ParameterBlock Id="b_gen" Text="General">
   <ParameterRefRef RefId="r_mode"/>
   <ParameterRefRef RefId="r_name"/>
   <ParameterRefRef RefId="r_note"/>
   <choose ParamRefId="r_order"><when test="DEMO-2"><ParameterRefRef RefId="r_extra"/></when></choose>
  </ParameterBlock></ChannelIndependentBlock>
  <Channel Id="c1" Number="1"><ParameterBlock Id="b_c1" Text="Channel 1">
   <choose ParamRefId="r_mode">
    <when test="dm_A;dm_B"><ParameterRefRef RefId="r_delay"/><ParameterRefRef RefId="r_fb"/></when>
    <when test="dm_B"><ParameterRefRef RefId="r_offset"/></when>
   </choose>
   <choose ParamRefId="r_fb">
    <when test="1"><Assign TargetParamRefRef="r_derived" Value="1"/></when>
   </choose>
  </ParameterBlock></Channel>
 </Dynamic></ApplicationProgram>""".encode()


def _description() -> Description:
    result = parse_description(DESCRIPTION)
    assert result is not None
    return result


def _configured(
    d: Description, edits: list[tuple[str, str]], globals_: dict[str, str]
) -> bytes:
    data = default_config(d, globals_)
    for ref, value in edits:
        data = edit(d, data, ref, value, globals_)
    return data


def _active_values(d: Description, data: bytes, globals_: dict[str, str]):
    settings = parse_settings(data)
    assert settings is not None
    state = evaluate(d, settings, globals_)
    return {r: state.values[r] for r in state.active_refs}, state.values


def test_the_memory_a_download_writes():
    d = _description()
    assert memory_range(d) == (_BASE, 11)
    compiled = compile_image(d, parse_settings(default_config(d)))
    assert compiled.used_length == 11
    assert image_of(compiled) == DeviceImage(_BASE, compiled.data[:11])


CONFIGURATIONS = [
    pytest.param([], {}, id="defaults"),
    pytest.param(
        [("r_mode", "dm_A"), ("r_delay", "1000"), ("r_name", "Hall")],
        {},
        id="enum-number-text",
    ),
    pytest.param(
        [("r_mode", "dm_B"), ("r_offset", "-3"), ("r_fb", "1"), ("r_name", "")],
        {},
        id="switches-active-refs",
    ),
    pytest.param(
        [("r_mode", "dm_A"), ("r_fb", "1"), ("r_extra", "-128")],
        {"DeviceOrderNumber": "DEMO-2"},
        id="global-variable",
    ),
]


@pytest.mark.parametrize(("edits", "globals_"), CONFIGURATIONS)
def test_the_memory_read_off_a_device_gives_its_configuration(edits, globals_):
    d = _description()
    configured = _configured(d, edits, globals_)
    compiled = compile_image(d, parse_settings(configured), globals_)

    recovered = decode_device(
        d, default_config(d, globals_), image_of(compiled), globals_
    )

    again = compile_image(d, parse_settings(recovered), globals_)
    assert again.mask == compiled.mask
    assert again.data[: again.used_length] == compiled.data[: compiled.used_length]
    active, values = _active_values(d, configured, globals_)
    recovered_active, recovered_values = _active_values(d, recovered, globals_)
    assert recovered_active == active
    # Nothing was switched off on the way: every setting is the same.
    assert recovered_values == values
    # A setting without memory keeps the product's default.
    assert recovered_values["r_note"] == "7"


def test_values_are_stored_as_an_edit_stores_them():
    d = _description()
    compiled = compile_image(
        d, parse_settings(_configured(d, [("r_mode", "dm_B"), ("r_fb", "1")], {}))
    )
    settings = parse_settings(decode_device(d, default_config(d), image_of(compiled)))
    assert settings is not None
    # By name where the default is a name, by value where the default is a value.
    assert settings.values["r_mode"] == "dm_B"
    assert settings.values["r_fb"] == "1"
    assert settings.values["r_derived"] == "1"


def test_a_setting_switched_off_keeps_the_products_default():
    d = _description()
    # The delay was changed, then hidden again: the device holds the default delay.
    configured = _configured(
        d, [("r_mode", "dm_A"), ("r_delay", "600"), ("r_mode", "dm_Off")], {}
    )
    compiled = compile_image(d, parse_settings(configured))
    recovered = parse_settings(decode_device(d, default_config(d), image_of(compiled)))
    assert recovered is not None
    assert recovered.values["r_mode"] == "dm_Off"
    # Not stored: the product's default applies.
    assert "r_delay" not in recovered.values


def _image(**changes: bytes) -> DeviceImage:
    d = _description()
    data = bytearray(compile_image(d, parse_settings(default_config(d))).data[:11])
    offsets = {"mode": 0, "delay": 1, "name": 3, "fb": 7, "derived": 8, "offset": 9}
    for name, raw in changes.items():
        data[offsets[name] : offsets[name] + len(raw)] = raw
    return DeviceImage(_BASE, bytes(data))


@pytest.mark.parametrize(
    "image",
    [
        # A delay that is not the default although the mode hides it.
        pytest.param(_image(delay=b"\x58\x02"), id="hidden-setting-changed"),
        # A value no choice of the mode has.
        pytest.param(_image(mode=b"\x07"), id="no-such-choice"),
        # A number beyond the type's maximum.
        pytest.param(_image(mode=b"\x01", delay=b"\xe9\x03"), id="out-of-range"),
        # A value set by a rule of the description that the rule does not set.
        pytest.param(_image(derived=b"\x01"), id="assigned-differs"),
        # A text with more after its end.
        pytest.param(_image(name=b"A\x00B\x00"), id="text-after-end"),
        pytest.param(DeviceImage(_BASE + 1, bytes(11)), id="other-base"),
        pytest.param(DeviceImage(_BASE, bytes(10)), id="other-length"),
    ],
)
def test_a_memory_no_configuration_gives_is_refused(image):
    d = _description()
    with pytest.raises(ValueError):
        decode_device(d, default_config(d), image)


class _Reader:
    def __init__(self, memory: dict[int, int]) -> None:
        self.memory = memory
        self.reads: list[tuple[int, int, bool]] = []

    async def read_memory(self, address: int, count: int, *, user: bool = False):
        self.reads.append((address, count, user))
        return bytes(self.memory.get(a, 0) for a in range(address, address + count))


def test_a_device_read_reads_exactly_the_parameter_memory():
    d = _description()
    compiled = compile_image(
        d, parse_settings(_configured(d, [("r_mode", "dm_A")], {}))
    )
    held = image_of(compiled)
    reader = _Reader({_BASE + i: b for i, b in enumerate(held.data)})
    image = asyncio.run(read_device_image(reader, d))
    assert reader.reads == [(_BASE, 11, False)]
    assert image == held


def test_a_description_without_memory_has_nothing_to_read():
    d = _description()
    d.parameters = {
        k: v for k, v in d.parameters.items() if v.offset is None or v.offset == 0
    }
    with pytest.raises(ValueError):
        memory_range(d)
