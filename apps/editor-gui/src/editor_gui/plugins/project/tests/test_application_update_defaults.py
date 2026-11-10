"""An application update carries the old defaults of the parameters the device holds no value for.

The new version changes defaults: a setting the user left unchanged keeps the old version's
default (also one with no access), a setting with the same default in both versions is not
touched, and a stored value wins over every default. Old defaults of parameters in a
calculation are set one after another with the stored values, in the order the old
application lists its ParameterRefs, each running its calculations when it changes a value.
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
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.application_indexer import ApplicationIndexer
from xknxeditor.prod.parser_v2.dynamic import DynamicUI
from xknxeditor.prod.script.errors import ParameterValidationError

OLD = "M-00FA_A-0D05-01-0000"
NEW = "M-00FA_A-0D05-02-0000"
OLD_PROGRAM = "M-00FA_H-1-1_HP-0D05-01-0000"
NEW_PROGRAM = "M-00FA_H-1-1_HP-0D05-02-0000"

# ParameterRef number -> (default of the old version, default of the new version).
_DEFAULTS = {
    1: ("0", "1"),  # Unchanged: a new default.
    2: ("3", "4"),  # No access: a new default.
    3: ("2", "2"),  # The same default.
    4: ("0", "1"),  # Stored: a new default.
    5: ("0", "0"),  # First (calculation input).
    6: ("0", "0"),  # Second (calculation input).
    7: ("1", "0"),  # Switch (calculation output): switched on by default before.
    8: ("1.5", "2.5"),  # A float.
}
_LISTED = (1, 2, 3, 4, 5, 6, 7, 8)
_SWITCH_FIRST = (7, 1, 2, 3, 4, 5, 6, 8)
# What the old device holds: the stored setting and the first calculation input.
STORED = {4: "2", 5: "1"}


def _xml(app: str, version: int, order: tuple[int, ...]) -> bytes:
    new = version == 2

    def ref(n: int) -> str:
        access = ' Access="None"' if n == 2 else ""
        return (
            f'<ParameterRef Id="{app}_P-{n}_R-{n}" RefId="{app}_P-{n}"{access}'
            f' Value="{_DEFAULTS[n][new]}" />'
        )

    def parameter(n: int, kind: str) -> str:
        memory = (
            f'<Memory CodeSegment="{app}_AS-0000" Offset="{n - 1}" BitOffset="0" />'
            if kind == "Mode"
            else ""
        )
        return (
            f'<Parameter Id="{app}_P-{n}" Name="Parameter{n}" ParameterType="{app}_PT-{kind}"'
            f' Text="Parameter {n}" Value="0">{memory}</Parameter>'
        )

    kinds = {8: "Float"}
    blocks = "".join(
        f'<ParameterRefRef RefId="{app}_P-{n}_R-{n}" />' for n in _LISTED if n != 2
    )
    return f"""<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
<ManufacturerData><Manufacturer RefId="M-00FA"><ApplicationPrograms>
<ApplicationProgram Id="{app}" ApplicationNumber="14" ApplicationVersion="{version}"
    ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Update defaults"
    LoadProcedureStyle="MergedProcedure" PeiType="0" DefaultLanguage="en-US"
    DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
<Static>
<Code><AbsoluteSegment Id="{app}_AS-0000" Address="0" Size="7"><Data>AAAAAAAAAA==</Data></AbsoluteSegment></Code>
<ParameterTypes>
<ParameterType Id="{app}_PT-Mode" Name="Mode"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Off" Value="0" Id="{app}_PT-Mode_EN-0" />
<Enumeration Text="Memory" Value="1" Id="{app}_PT-Mode_EN-1" />
<Enumeration Text="Timed" Value="2" Id="{app}_PT-Mode_EN-2" />
<Enumeration Text="Three" Value="3" Id="{app}_PT-Mode_EN-3" />
<Enumeration Text="Four" Value="4" Id="{app}_PT-Mode_EN-4" />
</TypeRestriction></ParameterType>
<ParameterType Id="{app}_PT-Switch" Name="Switch"><TypeRestriction Base="Value" SizeInBit="8">
<Enumeration Text="Inactive" Value="0" Id="{app}_PT-Switch_EN-0" />
<Enumeration Text="Active" Value="1" Id="{app}_PT-Switch_EN-1" />
</TypeRestriction></ParameterType>
<ParameterType Id="{app}_PT-Float" Name="Float">
<TypeFloat Encoding="IEEE-754 Single" minInclusive="0" maxInclusive="10" /></ParameterType>
</ParameterTypes>
<Parameters>
{"".join(parameter(n, kinds.get(n, "Switch" if n == 7 else "Mode")) for n in _LISTED)}
</Parameters>
<ParameterRefs>
{"".join(ref(n) for n in order)}
</ParameterRefs>
<ParameterCalculations>
<ParameterCalculation Id="{app}_PC-1" Name="Timed" Language="JavaScript">
<LRTransformation>if (first == 2 || second == 2) {{ on = 1; }}</LRTransformation>
<RLTransformation>if (on == 1) {{ first = 2; second = 2; }}</RLTransformation>
<LParameters>
<ParameterRefRef RefId="{app}_P-5_R-5" AliasName="first" />
<ParameterRefRef RefId="{app}_P-6_R-6" AliasName="second" />
</LParameters>
<RParameters><ParameterRefRef RefId="{app}_P-7_R-7" AliasName="on" /></RParameters>
</ParameterCalculation>
</ParameterCalculations>
</Static>
<Dynamic><ChannelIndependentBlock><ParameterBlock Id="{app}_PB-1" Text="General">
{blocks}
</ParameterBlock></ChannelIndependentBlock></Dynamic>
</ApplicationProgram></ApplicationPrograms></Manufacturer></ManufacturerData></KNX>""".encode()


def ref(app: str, n: int) -> str:
    return f"{app}_P-{n}_R-{n}"


def application(app: str, version: int, order: tuple[int, ...] = _LISTED) -> Any:
    return parse_application_xml(_xml(app, version, order), "M-00FA")[0]


def _carried(order: tuple[int, ...]) -> dict[str, str]:
    """The values stored for the updated device, from the old application listing its refs in
    ``order``."""
    old = ApplicationIndexer(application(OLD, 1, order).program)
    new_app = application(NEW, 2, order)
    indexer = ApplicationIndexer(new_app.program)
    stored = in_application_order([(ref(OLD, n), v) for n, v in STORED.items()], old)
    values = carry_parameters(stored, OLD, NEW, indexer).values
    defaults = unstored_defaults(
        list(old.parameter_refs), [r for r, _ in stored], OLD, old
    )
    everything = carry_parameters(
        in_application_order([*stored, *defaults], old), OLD, NEW, indexer
    ).values
    ui = DynamicUI(new_app.program)
    return dict(carry_values(ui, everything, {r for r, _ in values}).values)


def test_the_defaults_of_the_refs_without_a_value_are_listed_in_order() -> None:
    old = ApplicationIndexer(application(OLD, 1, _SWITCH_FIRST).program)
    defaults = unstored_defaults(list(old.parameter_refs), [ref(OLD, 4)], OLD, old)
    assert defaults == [(ref(OLD, n), _DEFAULTS[n][0]) for n in _SWITCH_FIRST if n != 4]


def test_an_unchanged_setting_keeps_the_old_default() -> None:
    values = _carried(_LISTED)
    assert values[ref(NEW, 1)] == "0"
    assert values[ref(NEW, 8)] == "1.5"


def test_a_setting_with_no_access_keeps_the_old_default() -> None:
    assert _carried(_LISTED)[ref(NEW, 2)] == "3"


def test_a_setting_with_the_same_default_is_not_touched() -> None:
    assert ref(NEW, 3) not in _carried(_LISTED)
    assert ref(NEW, 3) not in _carried(_SWITCH_FIRST)


def test_a_stored_value_wins_over_the_defaults() -> None:
    assert _carried(_LISTED)[ref(NEW, 4)] == "2"


def test_an_old_default_listed_last_runs_its_calculation_over_the_values_before() -> (
    None
):
    # The first input is set (1: the switch stays as it is), then the switch gets its old
    # default 1 back, whose calculation sets both inputs to 2.
    values = _carried(_LISTED)
    assert values[ref(NEW, 7)] == "1"
    assert values[ref(NEW, 5)] == "2"
    assert values[ref(NEW, 6)] == "2"


def test_an_old_default_listed_first_lets_the_values_after_it_back() -> None:
    # The switch's old default 1 sets both inputs to 2 first; the stored first input then
    # gets its value back and the second its old default.
    values = _carried(_SWITCH_FIRST)
    assert values[ref(NEW, 7)] == "1"
    assert values[ref(NEW, 5)] == "1"
    assert values[ref(NEW, 6)] == "0"


def test_an_old_default_the_new_device_cannot_take_is_left_out() -> None:
    ui = DynamicUI(application(NEW, 2).program)
    # Not a value of the new type, no such parameter (as in a module instance the new device
    # does not have), the value it has already, and one it takes as its type writes it.
    with pytest.raises(ParameterValidationError):
        ui.carry_value(ref(NEW, 1), "9")
    assert not ui.has_parameter(f"{NEW}_MD-1_M-1_MI-1_P-1_R-1")
    assert ui.has_parameter(ref(NEW, 1))
    assert ui.carry_value(ref(NEW, 8), "2.50") is None
    assert ui.carry_value(ref(NEW, 1), "02") == {ref(NEW, 1): ("1", "2")}
    assert ui.get_value(ref(NEW, 1)) == "2"
    # An unchanged setting the new type refuses keeps the new default.
    refused: list[tuple[str, str]] = []
    carried = carry_values(
        DynamicUI(application(NEW, 2).program),
        [(ref(NEW, 1), "9")],
        (),
        refused=lambda r, value, _message: refused.append((r, value)),
    )
    assert carried.values == []
    assert carried.not_taken == {ref(NEW, 1)}
    assert refused == [(ref(NEW, 1), "9")]


def target() -> ProgramSummary:
    return ProgramSummary(
        id=NEW_PROGRAM,
        hardware_id="M-00FA_H-1-1",
        application_id=NEW,
        application_name="Update defaults",
        application_number=14,
        application_version=2,
        registration_status="Registered",
    )


def project_with_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    project: ProjectService | None = None,
) -> ProjectService:
    """A project with a device of the old application holding ``STORED``, whose update target
    is the new one."""
    new = application(NEW, 2)
    if project is None:
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
    catalog = project._catalog  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(
        catalog, "get_application", lambda app_id: new if app_id == NEW else None
    )
    return project


def _stored(project: ProjectService) -> dict[str, str]:
    pid = project._pid  # pyright: ignore[reportPrivateUsage]
    assert pid is not None
    (row,) = project._svc.devices(pid)  # pyright: ignore[reportPrivateUsage]
    return {p.ref_id: p.value for p in row.parameters}


def test_the_update_stores_the_old_defaults_in_one_undo_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = project_with_device(tmp_path, monkeypatch)
    (device,) = project.devices
    result = project.update_application(device)
    assert result is not None and result.new_version == 2
    # The counts are those of the values the device held.
    assert result.kept == len(STORED)
    stored = _stored(project)
    assert {
        r: stored.get(ref(NEW, n)) for n, r in ((n, ref(NEW, n)) for n in _LISTED)
    } == {
        ref(NEW, 1): "0",
        ref(NEW, 2): "3",
        ref(NEW, 3): None,
        ref(NEW, 4): "2",
        ref(NEW, 5): "2",
        ref(NEW, 6): "2",
        ref(NEW, 7): "1",
        ref(NEW, 8): "1.5",
    }
    (updated,) = project.devices
    assert updated.get_param_value(ref(NEW, 3)) == "2"
    assert project.undo()
    (device,) = project.devices
    assert device.app is not None and device.app.id == OLD
    assert _stored(project) == {ref(OLD, n): v for n, v in STORED.items()}
    project.close()
