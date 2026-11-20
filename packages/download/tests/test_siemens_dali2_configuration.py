"""The ordinary parameter download finishes DALI-2 configuration on both lines."""

import pytest
from xknx.telegram.apci import FunctionPropertyCommand, PropertyValueRead

from xknxeditor.download.errors import DownloadError
from xknxeditor.download.image import DownloadImage
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_invoke_function_prop_t import (
    LdCtrlInvokeFunctionProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_proc_type_t import LdCtrlProcType

from .conftest import FakeDevice
from .test_procedure import _application


def controls(lines):
    result = []
    for command, pid in ((18, 57), (34, 58)):
        result.extend(
            LdCtrlInvokeFunctionProp(
                applies_to=LdCtrlProcType.FULL_PAR,
                obj_idx=16 + line,
                prop_id=52,
                inline_data=bytes((0, command)),
            )
            for line in range(lines)
        )
        result.extend(
            LdCtrlCompareProp(
                applies_to=LdCtrlProcType.FULL_PAR,
                obj_idx=16 + line,
                prop_id=pid,
                inline_data=b"\x01",
                invert=True,
                retry_interval=1,
                time_out=1,
            )
            for line in range(lines)
        )
        result.extend(
            LdCtrlCompareProp(
                applies_to=LdCtrlProcType.FULL_PAR,
                obj_idx=16 + line,
                prop_id=pid,
                inline_data=b"\x02",
            )
            for line in range(lines)
        )
    return result


class Gateway(FakeDevice):
    def __init__(self, failure=None):
        super().__init__(descriptor=0x07B0)
        self.failure = failure
        self.polls = {}
        self.configured = []

    async def request(self, payload, response_class):
        if isinstance(payload, FunctionPropertyCommand):
            pid = 57 if payload.data == b"\x00\x12" else 58
            self.properties[payload.object_index, pid] = b"\x01"
            self.polls[payload.object_index, pid] = 0
        if isinstance(payload, PropertyValueRead) and payload.property_id in (57, 58):
            key = payload.object_index, payload.property_id
            self.polls[key] += 1
            if self.polls[key] > 1:
                self.properties[key] = b"\x03" if self.failure == key else b"\x02"
                self.configured.append(key)
        return await super().request(payload, response_class)


@pytest.mark.parametrize("lines", [1, 2])
@pytest.mark.parametrize("scope", [DownloadScope.FULL, DownloadScope.PARAMETERS])
async def test_configuration_commands_poll_gear_and_inputs_after_parameter_load(
    lines, scope
):
    device = Gateway()
    application = _application(*controls(lines))
    runner = LoadProcedureRunner(
        application,
        DownloadImage(segments=(), properties=()),
        DeviceProgrammer(device),
        scope=scope,
    )
    await runner.run()
    commands = [
        (p.object_index, p.property_id, p.data)
        for p in device.sent
        if isinstance(p, FunctionPropertyCommand)
    ]
    assert commands == [
        (16 + line, 52, bytes((0, command)))
        for command in (18, 34)
        for line in range(lines)
    ]
    assert all(
        device.polls[16 + line, pid] == 3 for line in range(lines) for pid in (57, 58)
    )


@pytest.mark.parametrize("failure", [(16, 57), (17, 57), (16, 58), (17, 58)])
async def test_configuration_failure_aborts_instead_of_reporting_success(failure):
    device = Gateway(failure)
    runner = LoadProcedureRunner(
        _application(*controls(2)),
        DownloadImage(segments=(), properties=()),
        DeviceProgrammer(device),
    )
    with pytest.raises(DownloadError):
        await runner.run()
    if failure[1] == 57:
        assert all(
            p.data != b"\x00\x22"
            for p in device.sent
            if isinstance(p, FunctionPropertyCommand)
        )


async def test_group_only_download_does_not_reconfigure_dali_devices():
    device = Gateway()
    await LoadProcedureRunner(
        _application(*controls(2)),
        DownloadImage(segments=(), properties=()),
        DeviceProgrammer(device),
        scope=DownloadScope.GROUP_COMMUNICATION,
    ).run()
    assert not any(isinstance(p, FunctionPropertyCommand) for p in device.sent)
