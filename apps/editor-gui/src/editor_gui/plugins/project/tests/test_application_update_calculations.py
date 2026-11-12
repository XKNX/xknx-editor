"""An application update sets the values of parameters in a calculation one after another.

A switch is computed from two settings (LR: on when either is 2) and sets both to 2 when it is
switched on (RL). The old device holds the settings 1 and 2 and the switch on, as an edit of
the first setting leaves them. The update sets the values in the order the old application
lists its ParameterRefs, each running its calculations when it changes the value: a switch
the settings before it already switched on changes nothing, a switch listed before a setting
that switches it on writes over the settings listed before it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.application_update import (
    carry_parameters,
    carry_values,
    in_application_order,
)
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.catalog.core.hardware import ProgramSummary
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

_OLD = "M-00FA_A-0D04-01-0000"
_NEW = "M-00FA_A-0D04-02-0000"


def _xml(app: str, version: int, order: tuple[str, ...]) -> bytes:
    refs = {
        "first": f'<ParameterRef Id="{app}_P-1_R-1" RefId="{app}_P-1" />',
        "second": f'<ParameterRef Id="{app}_P-2_R-2" RefId="{app}_P-2" />',
        "switch": f'<ParameterRef Id="{app}_P-3_R-3" RefId="{app}_P-3" />',
        "other": f'<ParameterRef Id="{app}_P-4_R-4" RefId="{app}_P-4" />',
    }
    return f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{app}" ApplicationNumber="13" ApplicationVersion="{version}"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Update calculations"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{app}_AS-0000" Address="0" Size="3"><Data>AAAA</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{app}_PT-Mode" Name="Mode"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Off" Value="0" Id="{app}_PT-Mode_EN-0" />
<Enumeration Text="Memory" Value="1" Id="{app}_PT-Mode_EN-1" />
<Enumeration Text="Timed" Value="2" Id="{app}_PT-Mode_EN-2" />
</TypeRestriction></ParameterType>
<ParameterType Id="{app}_PT-Switch" Name="Switch"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Inactive" Value="0" Id="{app}_PT-Switch_EN-0" />
<Enumeration Text="Active" Value="1" Id="{app}_PT-Switch_EN-1" />
</TypeRestriction></ParameterType>
</ParameterTypes>
<Parameters>
<Parameter Id="{app}_P-1" Name="First" ParameterType="{app}_PT-Mode" Text="First" Value="0">
<Memory CodeSegment="{app}_AS-0000" Offset="0" BitOffset="0" /></Parameter>
<Parameter Id="{app}_P-2" Name="Second" ParameterType="{app}_PT-Mode" Text="Second" Value="0">
<Memory CodeSegment="{app}_AS-0000" Offset="1" BitOffset="0" /></Parameter>
<Parameter Id="{app}_P-3" Name="Switch" ParameterType="{app}_PT-Switch" Text="Switch" Value="0" />
<Parameter Id="{app}_P-4" Name="Other" ParameterType="{app}_PT-Mode" Text="Other" Value="0">
<Memory CodeSegment="{app}_AS-0000" Offset="2" BitOffset="0" /></Parameter>
</Parameters>
<ParameterRefs>
{"".join(refs[name] for name in order)}
</ParameterRefs>
<ParameterCalculations>
<ParameterCalculation Id="{app}_PC-1" Name="Timed" Language="JavaScript">
<LRTransformation>if (first == 2 || second == 2) {{ on = 1; }}</LRTransformation>
<RLTransformation>if (on == 1) {{ first = 2; second = 2; }}</RLTransformation>
<LParameters>
<ParameterRefRef RefId="{app}_P-1_R-1" AliasName="first" />
<ParameterRefRef RefId="{app}_P-2_R-2" AliasName="second" />
</LParameters>
<RParameters><ParameterRefRef RefId="{app}_P-3_R-3" AliasName="on" /></RParameters>
</ParameterCalculation>
</ParameterCalculations>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{app}_PB-1" Text="General">
<ParameterRefRef RefId="{app}_P-1_R-1" />
<ParameterRefRef RefId="{app}_P-2_R-2" />
<ParameterRefRef RefId="{app}_P-3_R-3" />
<ParameterRefRef RefId="{app}_P-4_R-4" />
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>""".encode()


_LISTED = ("first", "second", "switch", "other")
_SWITCH_FIRST = ("switch", "first", "second", "other")
_SWITCH_BETWEEN = ("first", "switch", "second", "other")


def _ref(app: str, n: int) -> str:
    return f"{app}_P-{n}_R-{n}"


def _app(app: str, version: int, order: tuple[str, ...]) -> Any:
    return parse_application_xml(_xml(app, version, order), "M-00FA")[0]


# The old device's values, the switch stored first (as an edit of the first setting stores
# the settings after the switch an earlier edit computed).
_STORED = [(3, "1"), (1, "1"), (2, "2"), (4, "2")]


def _replayed(order: tuple[str, ...]) -> dict[str, str]:
    old = _app(_OLD, 1, order)
    new = _app(_NEW, 2, order)
    indexer = ApplicationIndexer(new.program)
    rows = carry_parameters(
        in_application_order(
            [(_ref(_OLD, n), value) for n, value in _STORED],
            ApplicationIndexer(old.program),
        ),
        _OLD,
        _NEW,
        indexer,
    ).values
    ui = DynamicUI(new.program)
    return dict(carry_values(ui, rows, {ref for ref, _ in rows}).values)


def test_rows_follow_the_order_the_application_lists_its_refs() -> None:
    indexer = ApplicationIndexer(_app(_OLD, 1, _SWITCH_BETWEEN).program)
    rows = [(_ref(_OLD, n), str(n)) for n in (4, 2, 3, 1)]
    assert [ref for ref, _ in in_application_order(rows, indexer)] == [
        _ref(_OLD, n) for n in (1, 3, 2, 4)
    ]


def test_a_switch_its_settings_already_set_changes_nothing() -> None:
    assert _replayed(_LISTED) == {
        _ref(_NEW, 1): "1",
        _ref(_NEW, 2): "2",
        _ref(_NEW, 3): "1",
        _ref(_NEW, 4): "2",
    }


def test_a_switch_listed_first_gives_the_settings_after_it_back_their_values() -> None:
    values = _replayed(_SWITCH_FIRST)
    assert values[_ref(_NEW, 1)] == "1"
    assert values[_ref(_NEW, 2)] == "2"
    assert values[_ref(_NEW, 3)] == "1"


def test_a_switch_listed_before_the_setting_that_switches_it_on_writes_over_the_first() -> (
    None
):
    values = _replayed(_SWITCH_BETWEEN)
    assert values[_ref(_NEW, 1)] == "2"
    assert values[_ref(_NEW, 2)] == "2"
    assert values[_ref(_NEW, 3)] == "1"
    assert values[_ref(_NEW, 4)] == "2"


def _update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    old = _app(_OLD, 1, _LISTED)
    new = _app(_NEW, 2, _LISTED)
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    proj = ProjectService(cat)
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    assert proj.add_device("M-00FA_H-1-1_P-1", "M-00FA_H-1-1_HP-0D04-01-0000", "A", old)
    (device,) = proj.devices
    # Stored with the switch first, as the edits of the old device leave them.
    proj.edit_params(device, [(_ref(_OLD, n), v) for n, v in _STORED], mode="raw")
    target = ProgramSummary(
        id="M-00FA_H-1-1_HP-0D04-02-0000",
        hardware_id="M-00FA_H-1-1",
        application_id=_NEW,
        application_name="Update calculations",
        application_number=13,
        application_version=2,
        registration_status="Registered",
    )
    monkeypatch.setattr(proj, "local_update_target", lambda device: target)
    monkeypatch.setattr(
        cat, "get_application", lambda app_id: new if app_id == _NEW else None
    )
    (device,) = proj.devices
    assert proj.update_application(device) is not None
    (updated,) = proj.devices
    assert updated.app is not None and updated.app.id == _NEW
    values = {
        ref: updated.get_param_value(ref) or ""
        for ref in (_ref(_NEW, n) for n in range(1, 5))
    }
    proj.close()
    return values


def test_the_update_keeps_settings_their_switch_agrees_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _update(tmp_path, monkeypatch) == {
        _ref(_NEW, 1): "1",
        _ref(_NEW, 2): "2",
        _ref(_NEW, 3): "1",
        _ref(_NEW, 4): "2",
    }
