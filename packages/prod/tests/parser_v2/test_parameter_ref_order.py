"""Several active ParameterRefs of one parameter: a download applies them one after the other,
the refs without a numeric Tag by ref number first, then the others by tag, so the last one
sets the value (a converted Siemens application shows P-90 as R-5100, Tag 5100, and keeps
R-90, Tag 90, in a hidden block)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from xknxeditor.prod.parser_v2.encode import parameter_ref_order, resolve_param_values

APP = "M-00FD_A-001E-11-72B7"


def test_untagged_refs_come_first_by_number_then_tagged_ones_by_tag() -> None:
    refs = [
        (f"{APP}_P-90_R-5100", "5100"),
        (f"{APP}_P-90_R-90", "90"),
        (f"{APP}_P-90_R-12", None),
        (f"{APP}_P-90_R-3", ""),
        (f"{APP}_P-90_R-7", "x"),
    ]
    ordered = sorted(refs, key=lambda ref: parameter_ref_order(*ref))
    assert [ref for ref, _ in ordered] == [
        f"{APP}_P-90_R-3",
        f"{APP}_P-90_R-7",
        f"{APP}_P-90_R-12",
        f"{APP}_P-90_R-90",
        f"{APP}_P-90_R-5100",
    ]


def _state(values: dict[str, str], active: set[str]) -> Any:
    return SimpleNamespace(
        active_param_refs=lambda: active,
        get=values.get,
        relative_param_values=lambda: [],
    )


def test_the_last_active_ref_sets_the_value() -> None:
    hidden, shown, other = (
        f"{APP}_P-90_R-90",
        f"{APP}_P-90_R-5100",
        f"{APP}_P-91_R-91",
    )
    idx = cast(
        Any,
        SimpleNamespace(
            parameter_refs={
                hidden: SimpleNamespace(ref_id=f"{APP}_P-90", tag="90"),
                shown: SimpleNamespace(ref_id=f"{APP}_P-90", tag="5100"),
                other: SimpleNamespace(ref_id=f"{APP}_P-91", tag="91"),
            },
            parameters={},
        ),
    )
    values = {hidden: "0", shown: "1", other: "5"}
    both = resolve_param_values(idx, _state(values, {hidden, shown, other}))
    assert both == {f"{APP}_P-90": "1", f"{APP}_P-91": "5"}
    # An inactive ref does not count, wherever it comes.
    only_hidden = resolve_param_values(idx, _state(values, {hidden}))
    assert only_hidden == {f"{APP}_P-90": "0"}


def test_a_module_instance_takes_the_last_ref_too() -> None:
    from xknxeditor.prod.parser_v2.encode import (
        _build_instance_overrides,  # pyright: ignore[reportPrivateUsage]
    )

    md = f"{APP}_MD-1"
    first, last = f"{md}_P-1_R-1", f"{md}_P-1_R-2"
    idx = cast(
        Any,
        SimpleNamespace(
            parameter_refs={
                last: SimpleNamespace(ref_id=f"{md}_P-1", tag=None),
                first: SimpleNamespace(ref_id=f"{md}_P-1", tag=None),
            },
            parameters={f"{md}_P-1": SimpleNamespace(id=f"{md}_P-1")},
        ),
    )
    # Stored in the other order: the ref number decides, not where the value is.
    module = cast(Any, SimpleNamespace(param_ref_id_to_value={last: "2", first: "1"}))
    assert _build_instance_overrides(module, idx) == {f"{md}_P-1": "2"}


def test_ranks_are_the_last_active_ref_of_each_parameter() -> None:
    from xknxeditor.prod.parser_v2.encode import last_active_ref_ranks

    refs = [
        SimpleNamespace(id=f"{APP}_P-90_R-5100", ref_id=f"{APP}_P-90", tag="5100"),
        SimpleNamespace(id=f"{APP}_P-90_R-90", ref_id=f"{APP}_P-90", tag="90"),
        SimpleNamespace(id=f"{APP}_UP-7_R-7", ref_id=f"{APP}_UP-7", tag=None),
        SimpleNamespace(id=f"{APP}_UP-8_R-8", ref_id=f"{APP}_UP-8", tag=None),
    ]
    active = {f"{APP}_P-90_R-5100", f"{APP}_P-90_R-90", f"{APP}_UP-7_R-7"}
    assert last_active_ref_ranks(cast(Any, refs), active) == {
        f"{APP}_P-90": (1, 5100),
        f"{APP}_UP-7": (0, 7),
    }


def test_active_union_members_are_written_in_parameter_ref_order() -> None:
    """Zennio logic functions reference every member of a result union in one block, all
    hidden: the member with the last ref holds the shared byte, not the union's last member."""
    from xknxeditor.prod.parser_v2.encode import (
        _union_params_to_write,  # pyright: ignore[reportPrivateUsage]
    )

    members = [
        SimpleNamespace(id=f"{APP}_UP-{n}", default_union_parameter=False)
        for n in (170, 171, 172, 169)
    ]
    values = {up.id: str(i) for i, up in enumerate(members)}
    ranks = {f"{APP}_UP-{n}": (0, n) for n in (169, 170, 171, 172)}
    written = _union_params_to_write(cast(Any, members), values, True, ranks)
    assert [up.id for up, _ in written] == [
        f"{APP}_UP-{n}" for n in (169, 170, 171, 172)
    ]
    # Without ranks (no resolved state) the union's own order stays.
    assert [up.id for up, _ in _union_params_to_write(cast(Any, members), values)] == [
        up.id for up in members
    ]


def test_a_module_instance_downloads_the_value_of_its_last_active_ref() -> None:
    """A module parameter shown through two refs with their own values: the download writes
    the value of the last ref (by number), like the application's own parameters."""
    from xknxeditor.prod.application import parse_application_xml

    app_id = "M-00FA_A-00F4-01-0000"
    md, segment = f"{app_id}_MD-1", f"{app_id}_AS-0000"
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
    <ApplicationProgram Id="{app_id}" ApplicationNumber="244" ApplicationVersion="1"
        ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Module refs"
        LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
        DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
      <Static>
        <Code><AbsoluteSegment Id="{segment}" Address="0" Size="1"><Data>AA==</Data></AbsoluteSegment></Code>
        <ParameterTypes>
          <ParameterType Id="{app_id}_PT-N" Name="N"><TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" /></ParameterType>
        </ParameterTypes>
      </Static>
      <ModuleDefs>
        <ModuleDef Id="{md}" Name="Channel">
          <Arguments><Argument Id="{md}_A-1" Name="Offset" /></Arguments>
          <Static>
            <Parameters>
              <Parameter Id="{md}_P-1" Name="Mode" ParameterType="{app_id}_PT-N" Text="Mode" Value="0">
                <Memory CodeSegment="{segment}" Offset="0" BitOffset="0" BaseOffset="{md}_A-1" />
              </Parameter>
            </Parameters>
            <ParameterRefs>
              <ParameterRef Id="{md}_P-1_R-1" RefId="{md}_P-1" Value="5" Access="None" />
              <ParameterRef Id="{md}_P-1_R-2" RefId="{md}_P-1" Value="9" Access="None" />
            </ParameterRefs>
          </Static>
          <Dynamic>
            <ParameterBlock Id="{md}_PB-1" Text="Channel">
              <ParameterRefRef RefId="{md}_P-1_R-2" />
              <ParameterRefRef RefId="{md}_P-1_R-1" />
            </ParameterBlock>
          </Dynamic>
        </ModuleDef>
      </ModuleDefs>
      <Dynamic>
        <ChannelIndependentBlock><ParameterBlock Id="{app_id}_PB-1" Text="General" /></ChannelIndependentBlock>
        <Module Id="{md}_M-1" RefId="{md}" Name="Channel 1"><NumericArg RefId="{md}_A-1" Value="0" /></Module>
      </Dynamic>
    </ApplicationProgram>
  </ApplicationPrograms></Manufacturer></ManufacturerData>
</KNX>
"""
    application = parse_application_xml(xml.encode(), "M-00FA")[0]
    ui = application.dynamic_ui()
    assert ui is not None
    # The first ref listed is not the one that counts.
    assert ui.encode_to_memory()[segment] == bytes([9])
