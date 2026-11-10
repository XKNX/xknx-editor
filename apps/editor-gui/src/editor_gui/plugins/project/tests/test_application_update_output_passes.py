"""An application update evaluates each output a calculation writes as a change of its own.

The Assign writes 1 into the target while the first switch is on and the second off. The old
device holds the target 3 and both switches on: setting the first switch turns the Assign on,
which writes over the target, and setting the second one turns it off again. A calculation of the
second switch writes a result. Whether the values are set again (which gives the target its 3)
depends on the last change: the result the calculation writes after the switch stops no Assign
applying, so they are not. A calculation that fails leaves the switch set, but the update does
not check it at all, so the Assign the switch stopped applying does not make the values set
again either.
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
    unstored_defaults,
)
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.catalog.core.hardware import ProgramSummary
from xknxeditor.namespaces.intermediate import ParameterInstanceRef
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

OLD = "M-00FA_A-0D07-01-0000"
NEW = "M-00FA_A-0D07-02-0000"
OLD_PROGRAM = "M-00FA_H-1-1_HP-0D07-01-0000"
NEW_PROGRAM = "M-00FA_H-1-1_HP-0D07-02-0000"

# The parameters, in the order the application lists them: 1 the result the calculation of 4
# writes, 2 the target of the Assign, 3 and 4 the switches it applies under (3 on, 4 off).
RESULT, TARGET, FIRST, SECOND = 1, 2, 3, 4
STORED = {TARGET: "3", FIRST: "1", SECOND: "1"}

# What the calculation of the second switch does: write a result, or fail.
WRITES = "result = second + 1;"
FAILS = 'throw "broken";'
# Nothing to write: the Assign the second switch stops applying is the last change.
NOTHING = "result = result;"


def _xml(app: str, version: int, code: str) -> bytes:
    kinds = {RESULT: "Mode", TARGET: "Mode", FIRST: "Switch", SECOND: "Switch"}
    parameters = "".join(
        f'<Parameter Id="{app}_P-{n}" Name="P{n}" ParameterType="{app}_PT-{kind}"'
        f' Text="Parameter {n}" Value="0" />'
        for n, kind in kinds.items()
    )
    refs = "".join(
        f'<ParameterRef Id="{app}_P-{n}_R-{n}" RefId="{app}_P-{n}" />' for n in kinds
    )
    shown = "".join(f'<ParameterRefRef RefId="{app}_P-{n}_R-{n}" />' for n in kinds)
    enumeration = "".join(
        f'<Enumeration Text="Mode {n}" Value="{n}" Id="{app}_PT-Mode_EN-{n}" />'
        for n in range(5)
    )
    return f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{app}" ApplicationNumber="16" ApplicationVersion="{version}"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Update outputs"
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
<ParameterCalculations>
<ParameterCalculation Id="{app}_PC-1" Name="Result" Language="JavaScript">
<LRTransformation>{code}</LRTransformation>
<LParameters><ParameterRefRef RefId="{ref(app, SECOND)}" AliasName="second" /></LParameters>
<RParameters><ParameterRefRef RefId="{ref(app, RESULT)}" AliasName="result" /></RParameters>
</ParameterCalculation>
</ParameterCalculations>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{app}_PB-1" Text="General">
{shown}
<choose ParamRefId="{ref(app, FIRST)}"><when test="1">
<choose ParamRefId="{ref(app, SECOND)}"><when test="0">
<Assign TargetParamRefRef="{ref(app, TARGET)}" Value="1" />
</when></choose>
</when></choose>
</ParameterBlock>
</ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>""".encode()


def ref(app: str, n: int) -> str:
    return f"{app}_P-{n}_R-{n}"


def application(app: str, version: int, code: str) -> Any:
    return parse_application_xml(_xml(app, version, code), "M-00FA")[0]


def _carried(code: str, **told: Any) -> tuple[dict[str, str], set[str]]:
    """The values stored for the updated device, which held ``STORED``, and the refs of
    those not taken; ``told``: the callbacks of :func:`carry_values`."""
    old_ui = DynamicUI(
        application(OLD, 1, code).program,
        parameter_instance_refs=[
            ParameterInstanceRef(ref_id=ref(OLD, n), value=v) for n, v in STORED.items()
        ],
    )
    old = old_ui.indexer
    new_app = application(NEW, 2, code)
    indexer = ApplicationIndexer(new_app.program)
    rows = in_application_order([(ref(OLD, n), v) for n, v in STORED.items()], old)
    defaults = unstored_defaults(
        old_ui.parameter_ref_ids(), [r for r, _ in rows], OLD, old
    )
    everything = carry_parameters(
        in_application_order([*rows, *defaults], old), OLD, NEW, indexer
    ).values
    carried = carry_values(
        DynamicUI(new_app.program),
        everything,
        {r for r, _ in carry_parameters(rows, OLD, NEW, indexer).values},
        **told,
    )
    return dict(carried.values), carried.not_taken


def test_without_an_output_the_assign_stopped_last_sets_the_values_again() -> None:
    values, _ = _carried(NOTHING)
    assert values[ref(NEW, TARGET)] == "3"


def test_an_output_written_after_the_switch_is_the_last_change() -> None:
    # The switch stops the Assign applying, the result written after it stops none: the values
    # are not set again, and the target keeps the Assign's 1.
    values, _ = _carried(WRITES)
    assert values[ref(NEW, RESULT)] == "2"
    assert values[ref(NEW, TARGET)] == "1"
    assert values[ref(NEW, SECOND)] == "1"


def test_a_value_whose_calculation_fails_is_taken_but_not_checked() -> None:
    values, not_taken = _carried(FAILS)
    assert values[ref(NEW, SECOND)] == "1"
    assert ref(NEW, SECOND) not in not_taken
    assert values[ref(NEW, TARGET)] == "1"
    assert ref(NEW, RESULT) not in values


def test_a_failed_calculation_is_told() -> None:
    failed: list[tuple[str, str, str]] = []
    refused: list[str] = []
    _carried(
        FAILS,
        refused=lambda r, _value, _message: refused.append(r),
        failed=lambda r, value, message: failed.append((r, value, message)),
    )
    assert refused == []
    assert [(r, value) for r, value, _ in failed] == [(ref(NEW, SECOND), "1")]
    assert "broken" in failed[0][2]


def test_a_failed_calculation_is_logged_and_its_value_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    new = application(NEW, 2, FAILS)
    catalog = CatalogService(tmp_path / "c.xknxcatalog")
    project = ProjectService(catalog)
    project.set_logger(Logger(LogService(), "project"))
    project.new(tmp_path / "p.xknx")
    assert project.add_device(
        "M-00FA_H-1-1_P-1", OLD_PROGRAM, "A", application(OLD, 1, NOTHING)
    )
    (device,) = project.devices
    project.edit_params(
        device, [(ref(OLD, n), v) for n, v in STORED.items()], mode="raw"
    )
    target = ProgramSummary(
        id=NEW_PROGRAM,
        hardware_id="M-00FA_H-1-1",
        application_id=NEW,
        application_name="Update outputs",
        application_number=16,
        application_version=2,
        registration_status="Registered",
    )
    monkeypatch.setattr(project, "local_update_target", lambda device: target)
    monkeypatch.setattr(
        catalog, "get_application", lambda app_id: new if app_id == NEW else None
    )
    warnings: list[tuple[str, dict[str, Any]]] = []
    log = project._log  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(
        log, "warning", lambda message, **fields: warnings.append((message, fields))
    )
    (device,) = project.devices
    result = project.update_application(device)
    assert result is not None
    assert (result.kept, result.dropped) == (len(STORED), 0)
    assert [(m, f["parameter"], f["value"]) for m, f in warnings] == [
        (
            "application update: calculation of a value taken over failed",
            ref(NEW, SECOND),
            "1",
        )
    ]
    pid = project._pid  # pyright: ignore[reportPrivateUsage]
    assert pid is not None
    (row,) = project._svc.devices(pid)  # pyright: ignore[reportPrivateUsage]
    assert {p.ref_id: p.value for p in row.parameters} == {
        ref(NEW, TARGET): "1",
        ref(NEW, FIRST): "1",
        ref(NEW, SECOND): "1",
    }
    project.close()
