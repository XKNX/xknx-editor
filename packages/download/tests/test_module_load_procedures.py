"""The load procedures of a module: an ``LdCtrlMerge`` in the application's procedure runs the
procedure of that merge id of every module instance, once per instance and in the order of the
instances, each with its own arguments (``BaseOccurrence`` etc. add an argument's value) and
its own parameters (a ``choose``)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from xknx.telegram.apci import PropertyValueWrite

from xknxeditor.download.image import build_image
from xknxeditor.download.merge import resolve_download_controls
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.ld_ctrl_connect_t import LdCtrlConnect
from xknxeditor.namespaces.intermediate.ld_ctrl_invoke_function_prop_t import (
    LdCtrlInvokeFunctionProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_merge_t import LdCtrlMerge
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.module_instance_t import ModuleInstance
from xknxeditor.namespaces.intermediate.procedure_type_t import ProcedureType
from xknxeditor.namespaces.intermediate.resource_access_t import ResourceAccess
from xknxeditor.prod import Application
from xknxeditor.prod.application import parse_application_xml
from xknxeditor.prod.parser_v2.dynamic import DynamicUI

from .conftest import FakeDevice
from .test_merge import _application as _styled_application
from .test_merge import _fragment, _master_with_default

_APP = "M-00FA_A-0006-01-0000"
_NOTE = f"{_APP}_MD-1"
_NOTIFICATION = 50001

_XML = f"""<?xml version="1.0" encoding="utf-8"?>
<KNX xmlns="http://knx.org/xml/project/20" CreatedBy="test" ToolVersion="1">
  <ManufacturerData>
    <Manufacturer RefId="M-00FA">
      <ApplicationPrograms>
        <ApplicationProgram Id="{_APP}" ApplicationNumber="6" ApplicationVersion="1"
            ProgramType="ApplicationProgram" MaskVersion="MV-07B0" Name="Module procedures"
            LoadProcedureStyle="ProductProcedure" PeiType="0" DefaultLanguage="en-US"
            DynamicTableManagement="false" Linkable="false" MinEtsVersion="5.0">
          <Static>
            <ParameterTypes>
              <ParameterType Id="{_APP}_PT-N" Name="N">
                <TypeNumber SizeInBit="8" Type="unsignedInt" minInclusive="0" maxInclusive="255" />
              </ParameterType>
            </ParameterTypes>
            <LoadProcedures>
              <LoadProcedure>
                <LdCtrlConnect />
                <LdCtrlWriteProp ObjIdx="0" PropId="60" Verify="false" InlineData="01" />
                <LdCtrlMerge MergeId="106" />
                <LdCtrlWriteProp ObjIdx="0" PropId="60" Verify="false" InlineData="02" />
                <LdCtrlRestart />
              </LoadProcedure>
            </LoadProcedures>
            <Allocators>
              <Allocator Id="{_APP}_L-1" Name="Notification" Start="0" maxInclusive="15" />
            </Allocators>
          </Static>
          <ModuleDefs>
            <ModuleDef Id="{_NOTE}" Name="Notification">
              <Arguments>
                <Argument Id="{_NOTE}_A-1" Name="Index" Allocates="1" />
              </Arguments>
              <Static>
                <Parameters>
                  <Parameter Id="{_NOTE}_P-1" Name="Priority" ParameterType="{_APP}_PT-N" Text="Priority" Value="3">
                    <Property ObjectType="{_NOTIFICATION}" PropertyId="52" Offset="0" BitOffset="0" BaseOccurrence="{_NOTE}_A-1" />
                  </Parameter>
                  <Parameter Id="{_NOTE}_P-2" Name="Filter" ParameterType="{_APP}_PT-N" Text="Filter" Value="0">
                    <Property ObjectType="{_NOTIFICATION}" PropertyId="53" Offset="0" BitOffset="0" BaseOccurrence="{_NOTE}_A-1" />
                  </Parameter>
                </Parameters>
                <ParameterRefs>
                  <ParameterRef Id="{_NOTE}_P-1_R-1" RefId="{_NOTE}_P-1" />
                  <ParameterRef Id="{_NOTE}_P-2_R-2" RefId="{_NOTE}_P-2" />
                </ParameterRefs>
                <LoadProcedures>
                  <LoadProcedure MergeId="106">
                    <LdCtrlWriteProp ObjType="{_NOTIFICATION}" PropId="52" Verify="false" BaseOccurrence="{_NOTE}_A-1" />
                    <choose ParamRefId="{_NOTE}_P-2_R-2">
                      <when test="1">
                        <LdCtrlWriteProp ObjType="{_NOTIFICATION}" PropId="53" Verify="false" BaseOccurrence="{_NOTE}_A-1" />
                      </when>
                    </choose>
                    <LdCtrlInvokeFunctionProp ObjIdx="1" PropId="70" BaseObjIdx="{_NOTE}_A-1" InlineData="00" />
                  </LoadProcedure>
                </LoadProcedures>
              </Static>
              <Dynamic>
                <ParameterBlock Id="{_NOTE}_PB-1" Text="Notification">
                  <ParameterRefRef RefId="{_NOTE}_P-1_R-1" />
                  <ParameterRefRef RefId="{_NOTE}_P-2_R-2" />
                </ParameterBlock>
              </Dynamic>
            </ModuleDef>
          </ModuleDefs>
          <Dynamic>
            <ChannelIndependentBlock>
              <Repeat Id="{_APP}_X-1" Name="Notifications" Count="2">
                <Module Id="{_NOTE}_M-1" RefId="{_NOTE}">
                  <NumericArg RefId="{_NOTE}_A-1" AllocatorRefId="{_APP}_L-1" />
                </Module>
              </Repeat>
            </ChannelIndependentBlock>
          </Dynamic>
        </ApplicationProgram>
      </ApplicationPrograms>
    </Manufacturer>
  </ManufacturerData>
</KNX>
"""

_FIRST = f"{_NOTE}_M-1_MI-1"
_SECOND = f"{_NOTE}_M-1_MI-2"


def _application() -> Application:
    return parse_application_xml(_XML.encode(), "M-00FA")[0]


def _property_writes(
    controls: list[object],
) -> list[tuple[int | None, int, int, bytes | None]]:
    return [
        (c.obj_type, c.prop_id, c.occurrence, c.inline_data)
        for c in controls
        if isinstance(c, LdCtrlWriteProp)
    ]


def test_each_instance_runs_the_procedure_with_its_arguments_and_parameters() -> None:
    app = _application()
    image = build_image(app, parameter_values={f"{_SECOND}_P-2_R-2": "1"})
    controls = list(image.module_load_controls[106])
    # The first notification at occurrence 0 without a filter, the second at 1 with it.
    assert _property_writes(controls) == [
        (_NOTIFICATION, 52, 0, None),
        (_NOTIFICATION, 52, 1, None),
        (_NOTIFICATION, 53, 1, None),
    ]
    # A module's controls are the plain ones, the object index offset by the argument.
    assert {type(c) for c in controls} == {LdCtrlWriteProp, LdCtrlInvokeFunctionProp}
    assert [c.obj_idx for c in controls if isinstance(c, LdCtrlInvokeFunctionProp)] == [
        1,
        2,
    ]
    # The procedure runs them at the merge point.
    resolved = resolve_download_controls(
        app,
        parameter_values=image.procedure_parameters,
        module_controls=image.module_load_controls,
    )
    assert _property_writes(resolved) == [
        (None, 60, 0, b"\x01"),
        (_NOTIFICATION, 52, 0, None),
        (_NOTIFICATION, 52, 1, None),
        (_NOTIFICATION, 53, 1, None),
        (None, 60, 0, b"\x02"),
    ]
    assert not any(isinstance(c, LdCtrlMerge) for c in resolved)


def test_the_instances_run_in_their_order_not_in_the_stored_one() -> None:
    app = _application()
    ui = DynamicUI(
        app.program,
        module_instances=[
            ModuleInstance(id=_SECOND, ref_id=_NOTE),
            ModuleInstance(id=_FIRST, ref_id=_NOTE),
        ],
    )
    occurrences = [
        c.occurrence
        for c in ui.module_load_controls()[106]
        if isinstance(c, LdCtrlWriteProp)
    ]
    assert occurrences == [0, 1]


def test_a_module_without_procedures_has_none() -> None:
    start = _XML.index(
        '<LoadProcedures>\n                  <LoadProcedure MergeId="106">'
    )
    end = _XML.index("</LoadProcedures>", start) + len("</LoadProcedures>")
    app = parse_application_xml((_XML[:start] + _XML[end:]).encode(), "M-00FA")[0]
    image = build_image(app)
    assert image.module_load_controls == {}
    assert _property_writes(
        resolve_download_controls(app, module_controls=image.module_load_controls)
    ) == [(None, 60, 0, b"\x01"), (None, 60, 0, b"\x02")]


async def test_the_download_writes_the_properties_of_each_instance() -> None:
    app = _application()
    image = build_image(
        app,
        parameter_values={
            f"{_FIRST}_P-1_R-1": "7",
            f"{_SECOND}_P-1_R-1": "8",
            f"{_SECOND}_P-2_R-2": "1",
        },
    )
    # The device object 0, two notification objects, and the object the function goes to.
    device = FakeDevice(
        object_types={0: 0, 1: _NOTIFICATION, 2: _NOTIFICATION}, descriptor=0x07B0
    )
    for index in (1, 2):
        for pid in (52, 53):
            device.properties[index, pid] = b"\x00"
            device.property_element_sizes[index, pid] = 1
    device.property_element_sizes[0, 60] = 1
    programmer = DeviceProgrammer(device)
    programmer.skip_property_verify_probe()
    controls = [
        c
        for c in resolve_download_controls(
            app,
            parameter_values=image.procedure_parameters,
            module_controls=image.module_load_controls,
        )
        if not isinstance(c, LdCtrlInvokeFunctionProp)
    ]
    await LoadProcedureRunner(app, image, programmer, controls=controls).run()
    written = [
        (w.object_index, w.property_id, w.data)
        for w in device.sent
        if isinstance(w, PropertyValueWrite)
    ]
    assert written == [
        (0, 60, b"\x01"),
        (1, 52, b"\x07"),
        (2, 52, b"\x08"),
        (2, 53, b"\x01"),
        (0, 60, b"\x02"),
    ]


_MODULE_WRITE = LdCtrlWriteProp(
    obj_type=_NOTIFICATION, prop_id=52, occurrence=1, verify=False
)


def test_a_merged_procedure_runs_the_module_controls_inside_its_fragment() -> None:
    # The application's fragment (merge id 6) names the modules' merge id 106.
    master = _master_with_default(
        LdCtrlConnect(), LdCtrlMerge(merge_id=6), LdCtrlRestart()
    )
    application = _styled_application(
        LoadProcedureStyle.MERGED_PROCEDURE, _fragment(6, LdCtrlMerge(merge_id=106))
    )
    for scope in (DownloadScope.FULL, DownloadScope.PARAMETERS):
        controls = resolve_download_controls(
            cast(Any, application),
            master,
            scope=scope,
            module_controls={106: [_MODULE_WRITE]},
        )
        assert [type(c).__name__ for c in controls] == [
            "LdCtrlConnect",
            "LdCtrlWriteProp",
            "LdCtrlRestart",
        ]
        # A module's controls are the manufacturer's: never left out as automatic ones.
        assert controls[1].applies_to is LdCtrlProcType.ALL  # type: ignore[attr-defined]
    # Without module instances the merge point is left, and does nothing.
    assert [
        type(c).__name__
        for c in resolve_download_controls(cast(Any, application), master)
    ] == ["LdCtrlConnect", "LdCtrlMerge", "LdCtrlRestart"]


def test_the_unload_procedure_runs_them_at_its_merge_point_too() -> None:
    unload = SimpleNamespace(
        procedure_type=ProcedureType.UNLOAD,
        procedure_sub_type=LdCtrlProcType.ALL,
        access=[ResourceAccess.REMOTE],
        choice=[LdCtrlConnect(), LdCtrlMerge(merge_id=106), LdCtrlRestart()],
    )
    master = _master_with_default(LdCtrlConnect())
    configuration = master.mask_versions.mask_version[0].hawk_configuration_data[0]  # type: ignore[union-attr]
    configuration.procedures.procedure.append(unload)  # type: ignore[union-attr]
    application = _styled_application(LoadProcedureStyle.MERGED_PROCEDURE)
    controls = resolve_download_controls(
        cast(Any, application),
        master,
        procedure_type=ProcedureType.UNLOAD,
        module_controls={106: [_MODULE_WRITE]},
    )
    assert [type(c).__name__ for c in controls] == [
        "LdCtrlConnect",
        "LdCtrlWriteProp",
        "LdCtrlRestart",
    ]
