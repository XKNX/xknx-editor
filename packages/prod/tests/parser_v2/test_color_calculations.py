"""Colours and data in ParameterCalculations: the values scripts see and the values they set."""

from __future__ import annotations

import pytest

from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.script import CalculationError

APP = "M-00FA_A-0003-01-0000"
DAY, NIGHT, DAY565, NIGHT565, SET, DATA, COUNT = (
    f"{APP}_P-{n}_R-{n}" for n in range(1, 8)
)

XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{APP}" ApplicationNumber="3" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Colors"
            LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <ParameterTypes>
              <ParameterType Id="{APP}_PT-C" Name="C"><TypeColor Space="RGB" /></ParameterType>
              <ParameterType Id="{APP}_PT-N" Name="N">
                <TypeNumber SizeInBit="16" Type="unsignedInt" minInclusive="0" maxInclusive="65535" />
              </ParameterType>
              <ParameterType Id="{APP}_PT-D" Name="D"><TypeRawData MaxSize="8" /></ParameterType>
            </ParameterTypes>
            <Parameters>
              <Parameter Id="{APP}_P-1" Name="Day" ParameterType="{APP}_PT-C" Text="Day" Value="#D6D2D6" />
              <Parameter Id="{APP}_P-2" Name="Night" ParameterType="{APP}_PT-C" Text="Night" Value="#191C19" />
              <Parameter Id="{APP}_P-3" Name="Day565" ParameterType="{APP}_PT-N" Text="Day565" Value="0" />
              <Parameter Id="{APP}_P-4" Name="Night565" ParameterType="{APP}_PT-N" Text="Night565" Value="0" />
              <Parameter Id="{APP}_P-5" Name="Set" ParameterType="{APP}_PT-N" Text="Set" Value="0" />
              <Parameter Id="{APP}_P-6" Name="Data" ParameterType="{APP}_PT-D" Text="Data" Value="AQID" />
              <Parameter Id="{APP}_P-7" Name="Count" ParameterType="{APP}_PT-N" Text="Count" Value="0" />
            </Parameters>
            <ParameterRefs>
              {"".join(f'<ParameterRef Id="{APP}_P-{n}_R-{n}" RefId="{APP}_P-{n}" />' for n in range(1, 8))}
            </ParameterRefs>
            <ParameterCalculations>
              <ParameterCalculation Id="{APP}_PC-1" Language="JavaScript" Name="RGB888toRGB565">
                <RLTransformation>Day = 0x000000; Night = 0x949294</RLTransformation>
                <LRTransformation>
function to565(c) {{
  var r = ((c &amp; 0xFF0000) &gt;&gt; 16) * 249 + 1014 &gt;&gt; 11;
  var g = ((c &amp; 0xFF00) &gt;&gt; 8) * 253 + 505 &gt;&gt; 10;
  var b = (c &amp; 0xFF) * 249 + 1014 &gt;&gt; 11;
  return r &lt;&lt; 11 | g &lt;&lt; 5 | b;
}}
Day565 = to565(Day); Night565 = to565(Night);</LRTransformation>
                <LParameters>
                  <ParameterRefRef RefId="{DAY}" AliasName="Day" />
                  <ParameterRefRef RefId="{NIGHT}" AliasName="Night" />
                </LParameters>
                <RParameters>
                  <ParameterRefRef RefId="{DAY565}" AliasName="Day565" />
                  <ParameterRefRef RefId="{NIGHT565}" AliasName="Night565" />
                </RParameters>
              </ParameterCalculation>
              <ParameterCalculation Id="{APP}_PC-2" Language="JavaScript" Name="Set"
                  LRTransformationFunc="setColor" RLTransformationFunc="nothing">
                <LParameters><ParameterRefRef RefId="{SET}" AliasName="n" /></LParameters>
                <RParameters><ParameterRefRef RefId="{NIGHT}" AliasName="c" /></RParameters>
              </ParameterCalculation>
              <ParameterCalculation Id="{APP}_PC-3" Language="JavaScript" Name="Count"
                  LRTransformationFunc="count" RLTransformationFunc="nothing">
                <LParameters><ParameterRefRef RefId="{DATA}" AliasName="d" /></LParameters>
                <RParameters><ParameterRefRef RefId="{COUNT}" AliasName="n" /></RParameters>
              </ParameterCalculation>
            </ParameterCalculations>
            <Script>
var colors = [
  0, 0x12345678, -1, 2147483648, 0.5, 1.5, "#FF0000", "00ff00", "#FF000", " 42 ", true, undefined
];
function setColor(input, output) {{ output.c = colors[input.n]; }}
function count(input, output) {{ output.n = input.d.length * 100 + input.d[0] + input.d[2]; }}
function nothing(input, output) {{}}
</Script>
          </Static>
          <Dynamic><ChannelIndependentBlock><ParameterBlock Id="{APP}_PB-1" Name="B" Text="B">
            {"".join(f'<ParameterRefRef RefId="{APP}_P-{n}_R-{n}" />' for n in range(1, 8))}
          </ParameterBlock></ChannelIndependentBlock></Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""


@pytest.fixture
def ui() -> DynamicUI:
    app = parse_application_xml(XML.encode(), "M-00FA")[0]
    dui = DynamicUI(app.program)
    dui.ui()
    return dui


def test_a_script_sees_a_colour_as_its_argb_number(ui: DynamicUI) -> None:
    # 0xD6D2D6 gives 54938 (the bits of each channel, not the text "#D6D2D6").
    assert ui.edit_parameter(DAY, "#D6D2D7") == {
        DAY: ("#D6D2D6", "#D6D2D7"),
        DAY565: ("0", "54938"),
        NIGHT565: ("0", "6371"),
    }
    # Lower case hex digits are a colour too.
    ui.edit_parameter(NIGHT, "1a1c19")
    assert ui.get_value(NIGHT565) == "6371"
    assert ui.edit_parameter(NIGHT, "#FFFFFF") == {
        NIGHT: ("1a1c19", "#FFFFFF"),
        NIGHT565: ("6371", "65535"),
    }


def test_a_number_a_script_sets_is_stored_as_an_upper_case_colour(
    ui: DynamicUI,
) -> None:
    changes = ui.edit_parameter(DAY565, "1")
    assert changes[DAY] == ("#D6D2D6", "#000000")
    assert changes[NIGHT] == ("#191C19", "#949294")


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        # The red, green and blue of a 32-bit ARGB number; the alpha is dropped.
        (1, "#345678"),
        (2, "#FFFFFF"),
        # Rounded half to even.
        (4, "#000000"),
        (5, "#000002"),
        # A text of six upper case hex digits is kept as it is.
        (6, "#FF0000"),
        # A text of an integer is that number.
        (9, "#00002A"),
        (10, "#000001"),
    ],
)
def test_colours_a_script_sets(ui: DynamicUI, index: int, expected: str) -> None:
    ui.edit_parameter(SET, str(index))
    assert ui.get_value(NIGHT) == expected


@pytest.mark.parametrize(
    ("index", "message"),
    [
        # Beyond 32 bits.
        (3, "The given type is not supported."),
        (7, "The given string is not a valid input RGB string."),
        (8, "The given string is not a valid input RGB string."),
    ],
)
def test_colours_a_script_cannot_set(ui: DynamicUI, index: int, message: str) -> None:
    with pytest.raises(CalculationError) as err:
        ui.edit_parameter(SET, str(index))
    assert str(err.value) == message
    assert ui.get_value(NIGHT) == "#191C19"


def test_no_colour_leaves_the_colour_as_it_is(ui: DynamicUI) -> None:
    changes = ui.edit_parameter(SET, "11")
    assert NIGHT not in changes
    assert ui.get_value(NIGHT) == "#191C19"


def test_a_script_sees_data_as_an_array_of_octets(ui: DynamicUI) -> None:
    assert ui.edit_parameter(DATA, "CgsM") == {
        DATA: ("AQID", "CgsM"),
        COUNT: ("0", "322"),
    }
