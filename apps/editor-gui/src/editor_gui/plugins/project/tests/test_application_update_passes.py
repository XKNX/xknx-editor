"""An application update sets the old values on the new device as edits set them.

A value the new parameter type refuses is left out (and logged): the new version's
enumeration no longer has it. Where setting a value stops an Assign applying, the values are
set again: a selector set after the Assign's target first switches the Assign on, which writes
over the target, and the next one switches it off again, which leaves the target as the Assign
wrote it, until the target is set again. A module parameter that takes the value of an argument
of its module instance carries that value, not its own default.
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
    module_instances,
    unstored_defaults,
    with_argument_values,
)
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.catalog.core.hardware import ProgramSummary
from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

OLD = "M-00FA_A-0D06-01-0000"
NEW = "M-00FA_A-0D06-02-0000"
OLD_PROGRAM = "M-00FA_H-1-1_HP-0D06-01-0000"
NEW_PROGRAM = "M-00FA_H-1-1_HP-0D06-02-0000"

# The parameters: 1 a mode the new version has fewer values of, 2 another one, 3 the target
# of the Assign, 4 and 5 the switches it applies under (4 on, 5 off).
MODE, OTHER, TARGET, FIRST, SECOND = 1, 2, 3, 4, 5
# What the old device holds: a mode the new version does not have, the Assign off.
STORED = {MODE: "4", OTHER: "2", TARGET: "3", FIRST: "1", SECOND: "1"}


def _xml(app: str, version: int) -> bytes:
    modes = 5 if version == 1 else 4
    enumeration = "".join(
        f'<Enumeration Text="Mode {n}" Value="{n}" Id="{app}_PT-Mode_EN-{n}" />'
        for n in range(modes)
    )
    kinds = {
        MODE: "Mode",
        OTHER: "Mode",
        TARGET: "Mode",
        FIRST: "Switch",
        SECOND: "Switch",
    }
    parameters = "".join(
        f'<Parameter Id="{app}_P-{n}" Name="P{n}" ParameterType="{app}_PT-{kind}"'
        f' Text="Parameter {n}" Value="0" />'
        for n, kind in kinds.items()
    )
    refs = "".join(
        f'<ParameterRef Id="{app}_P-{n}_R-{n}" RefId="{app}_P-{n}" />' for n in kinds
    )
    shown = "".join(f'<ParameterRefRef RefId="{app}_P-{n}_R-{n}" />' for n in kinds)
    md = f"{app}_MD-1"
    return f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{app}" ApplicationNumber="15" ApplicationVersion="{version}"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Update passes"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<ParameterTypes>
<ParameterType Id="{app}_PT-Mode" Name="Mode"><TypeRestriction Base="Value" SizeInBit="8">
{enumeration}
</TypeRestriction></ParameterType>
<ParameterType Id="{app}_PT-Switch" Name="Switch"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Off" Value="0" Id="{app}_PT-Switch_EN-0" />
<Enumeration Text="On" Value="1" Id="{app}_PT-Switch_EN-1" />
</TypeRestriction></ParameterType>
</ParameterTypes>
<Parameters>{parameters}</Parameters>
<ParameterRefs>{refs}</ParameterRefs>
</Static>
<ModuleDefs>
<ModuleDef Id="{md}" Name="Channel">
<Arguments><Argument Id="{md}_A-1" Name="Kind" /></Arguments>
<Static>
<Parameters>
<Parameter Id="{md}_P-1" Name="Kind" ParameterType="{app}_PT-Switch" Text="Kind" Value="1"
    BaseValue="{md}_A-1" />
</Parameters>
<ParameterRefs><ParameterRef Id="{md}_P-1_R-1" RefId="{md}_P-1" /></ParameterRefs>
</Static>
<Dynamic><ParameterBlock Id="{md}_PB-1" Text="Channel">
<ParameterRefRef RefId="{md}_P-1_R-1" />
</ParameterBlock></Dynamic>
</ModuleDef>
</ModuleDefs>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{app}_PB-1" Text="General">
{shown}
<choose ParamRefId="{app}_P-{FIRST}_R-{FIRST}"><when test="1">
<choose ParamRefId="{app}_P-{SECOND}_R-{SECOND}"><when test="0">
<Assign TargetParamRefRef="{app}_P-{TARGET}_R-{TARGET}" Value="1" />
</when></choose>
</when></choose>
</ParameterBlock>
<Module Id="{md}_M-1" RefId="{md}" Name="A"><NumericArg RefId="{md}_A-1" Value="0" /></Module>
</ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>""".encode()


def ref(app: str, n: int) -> str:
    return f"{app}_P-{n}_R-{n}"


def kind(app: str) -> str:
    """The module instance's parameter that takes the value of its argument."""
    return f"{app}_MD-1_M-1_MI-1_P-1_R-1"


def application(app: str, version: int) -> Any:
    return parse_application_xml(_xml(app, version), "M-00FA")[0]


def _carried(
    stored: dict[int, str], refused: list[str] | None = None, *, arguments: bool = True
) -> dict[str, str]:
    """The values stored for the updated device, which held ``stored``."""
    old_ui = DynamicUI(
        application(OLD, 1).program,
        parameter_instance_refs=[
            ParameterInstanceRef(ref_id=ref(OLD, n), value=v) for n, v in stored.items()
        ],
    )
    old = old_ui.indexer
    new_app = application(NEW, 2)
    indexer = ApplicationIndexer(new_app.program)
    rows = in_application_order([(ref(OLD, n), v) for n, v in stored.items()], old)
    old_refs = old_ui.parameter_ref_ids()
    defaults = unstored_defaults(old_refs, [r for r, _ in rows], OLD, old)
    old_values = in_application_order([*rows, *defaults], old)
    if arguments:
        old_values = with_argument_values(old_values, old_ui)
    everything = carry_parameters(old_values, OLD, NEW, indexer).values
    carried = carry_values(
        DynamicUI(new_app.program),
        everything,
        {r for r, _ in carry_parameters(rows, OLD, NEW, indexer).values},
        module_instances(old_refs, OLD, NEW),
        lambda r, _value, _message: (refused if refused is not None else []).append(r),
    )
    return dict(carried.values)


def test_a_stored_value_the_new_type_refuses_is_dropped() -> None:
    refused: list[str] = []
    values = _carried({MODE: "4", OTHER: "2"}, refused)
    assert ref(NEW, MODE) not in values
    assert values[ref(NEW, OTHER)] == "2"
    assert refused == [ref(NEW, MODE)]


def test_a_value_that_stops_an_assign_applying_sets_the_values_again() -> None:
    # The first switch turns the Assign on, which writes 1 over the target set before; the
    # second turns it off and leaves the 1; the values set again give the target its 3.
    values = _carried({TARGET: "3", FIRST: "1", SECOND: "1"})
    assert values[ref(NEW, TARGET)] == "3"
    assert values[ref(NEW, FIRST)] == "1"
    assert values[ref(NEW, SECOND)] == "1"


def test_an_assign_still_applying_keeps_its_value() -> None:
    # The second switch stays off: the Assign applies at the end, and nothing is set again.
    values = _carried({TARGET: "3", FIRST: "1"})
    assert values[ref(NEW, TARGET)] == "1"


def test_a_module_parameter_carries_the_value_of_its_argument() -> None:
    # The parameter's own default is 1, the instance's argument 0: the old device's parameter
    # has 0, which the new device takes as its own value.
    assert _carried({})[kind(NEW)] == "0"
    assert kind(NEW) not in _carried({}, arguments=False)


def test_the_instances_are_named_as_in_the_new_application() -> None:
    refs = [ref(OLD, 1), kind(OLD), f"{OLD}_MD-1_M-1_MI-1_SM-1_M-1_MI-1-1_P-2_R-2"]
    assert module_instances(refs, OLD, NEW) == [
        f"{NEW}_MD-1_M-1_MI-1",
        f"{NEW}_MD-1_M-1_MI-1_SM-1_M-1_MI-1-1",
    ]


def test_the_values_of_an_instance_the_new_device_lacks_are_not_taken() -> None:
    missing = f"{NEW}_MD-1_M-1_MI-2_P-1_R-1"
    carried = carry_values(
        DynamicUI(application(NEW, 2).program), [(missing, "0")], {missing}
    )
    assert carried.values == []
    assert carried.not_taken == {missing}


def target() -> ProgramSummary:
    return ProgramSummary(
        id=NEW_PROGRAM,
        hardware_id="M-00FA_H-1-1",
        application_id=NEW,
        application_name="Update passes",
        application_number=15,
        application_version=2,
        registration_status="Registered",
    )


def test_the_update_takes_over_what_the_new_device_accepts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    new = application(NEW, 2)
    catalog = CatalogService(tmp_path / "c.xknxcatalog")
    project = ProjectService(catalog)
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "p.xknx")
    assert project.add_device("M-00FA_H-1-1_P-1", OLD_PROGRAM, "A", application(OLD, 1))
    (device,) = project.devices
    project.edit_params(
        device, [(ref(OLD, n), v) for n, v in STORED.items()], mode="raw"
    )
    monkeypatch.setattr(project, "local_update_target", lambda device: target())
    monkeypatch.setattr(
        catalog, "get_application", lambda app_id: new if app_id == NEW else None
    )
    warnings: list[dict[str, Any]] = []
    log = project._log  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(
        log, "warning", lambda message, **fields: warnings.append(fields)
    )
    (device,) = project.devices
    result = project.update_application(device)
    assert result is not None
    # The mode the new version does not have is dropped, the other values are kept.
    assert (result.kept, result.dropped) == (len(STORED) - 1, 1)
    assert [w["parameter"] for w in warnings] == [ref(NEW, MODE)] * 3
    pid = project._pid  # pyright: ignore[reportPrivateUsage]
    assert pid is not None
    (row,) = project._svc.devices(pid)  # pyright: ignore[reportPrivateUsage]
    assert {p.ref_id: p.value for p in row.parameters} == {
        ref(NEW, OTHER): "2",
        ref(NEW, TARGET): "3",
        ref(NEW, FIRST): "1",
        ref(NEW, SECOND): "1",
        kind(NEW): "0",
    }
    project.close()
