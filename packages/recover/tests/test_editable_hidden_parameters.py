from xknxeditor.prod.application import parse_application_xml
from xknxeditor.recover import RecoveryEffort, recover_parameters

APP = "M-00FA_A-1234-01-0000"
SEGMENT = APP + "_AS-0000"
REF = APP + "_P-1_R-1"


def test_only_declared_hidden_parameter_is_recovered_without_changing_catalog():
    xml = f'''<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
    <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{APP}" ApplicationNumber="4660" ApplicationVersion="1"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Test"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
    <Static><Code><AbsoluteSegment Id="{SEGMENT}" Address="0" Size="2"><Data>AAA=</Data></AbsoluteSegment></Code>
    <ParameterTypes><ParameterType Id="{APP}_PT-1" Name="Byte"><TypeNumber SizeInBit="8"
    Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType></ParameterTypes>
    <Parameters>{"".join(f'<Parameter Id="{APP}_P-{i}" Name="Hidden{i}" ParameterType="{APP}_PT-1" Text="" Value="0" Access="None"><Memory CodeSegment="{SEGMENT}" Offset="{i - 1}" BitOffset="0" /></Parameter>' for i in (1, 2))}</Parameters>
    <ParameterRefs>{"".join(f'<ParameterRef Id="{APP}_P-{i}_R-{i}" RefId="{APP}_P-{i}" />' for i in (1, 2))}</ParameterRefs>
    </Static><Dynamic><ChannelIndependentBlock><ParameterBlock Id="{APP}_PB-1" Text="Test">
    <ParameterRefRef RefId="{REF}" /><ParameterRefRef RefId="{APP}_P-2_R-2" />
    </ParameterBlock></ChannelIndependentBlock></Dynamic>
    </ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>'''
    app = parse_application_xml(xml.encode(), "M-00FA")[0]
    for editable in ((), (REF,), ()):
        result = recover_parameters(
            app,
            {SEGMENT: b"\x2a\x07"},
            effort=RecoveryEffort.FAST,
            editable_parameter_refs=editable,
        )
        assert result.values.get(REF) == ("42" if editable else None)
        assert APP + "_P-2_R-2" not in result.values
    assert app.dynamic_ui().get_value(REF) == "0"
