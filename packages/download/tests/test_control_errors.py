"""Typed control failures, explicit handlers, and read-only previews."""

from __future__ import annotations

import pytest
from xknx.telegram.apci import (
    MemoryWrite,
    PropertyValueRead,
    PropertyValueResponse,
    PropertyValueWrite,
)

from xknxeditor.download.errors import (
    CompareMismatch,
    PropertyAccessRejected,
    VerificationError,
)
from xknxeditor.download.image import DownloadImage
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.namespaces.intermediate.application_program_static_t_messages import (
    ApplicationProgramStaticMessages,
)
from xknxeditor.namespaces.intermediate.application_program_static_t_messages_message import (
    ApplicationProgramStaticMessagesMessage,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_base_t_on_error import LdCtrlBaseOnError
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_mem_t import LdCtrlCompareMem
from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import LdCtrlCompareProp
from xknxeditor.namespaces.intermediate.ld_ctrl_error_cause_t import LdCtrlErrorCause
from xknxeditor.namespaces.intermediate.ld_ctrl_map_error_t import LdCtrlMapError

from .conftest import FakeDevice
from .test_wire_audit import seeded_application

IGNORE = LdCtrlBaseOnError(cause=LdCtrlErrorCause.COMPARE_MISMATCH, ignore=True)


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("memory", [False, True])
async def test_first_matching_handler_continues_and_reports_progress(preview, memory):
    app, _ = seeded_application()
    control = (
        LdCtrlCompareMem(address=0x100, size=1, inline_data=b"x")
        if memory
        else LdCtrlCompareProp(obj_idx=0, prop_id=13, inline_data=b"x")
    )
    control.on_error = [
        LdCtrlBaseOnError(cause=LdCtrlErrorCause.RESOURCE_NOT_FOUND),
        IGNORE,
        LdCtrlBaseOnError(cause=LdCtrlErrorCause.COMPARE_MISMATCH),
    ]
    device = FakeDevice()
    device.properties[0, 13] = b"y"
    runner = LoadProcedureRunner(
        app, DownloadImage((), ()), DeviceProgrammer(device), controls=[control]
    )
    progress = []
    if preview:
        await runner.preflight()
    else:
        await runner.run(lambda done, total: progress.append((done, total)))
        assert progress == [(1, 1)]
    assert not any(
        isinstance(p, (MemoryWrite, PropertyValueWrite)) for p in device.sent
    )


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize(
    "failure", ["empty", "truncated", "metadata", "transport", "pid7"]
)
async def test_compare_handler_does_not_hide_invalid_response(preview, failure):
    class InvalidDevice(FakeDevice):
        async def request(self, payload, expected):
            if failure == "transport":
                raise TimeoutError("response timed out")
            assert isinstance(payload, PropertyValueRead)
            return self._telegram(
                PropertyValueResponse(
                    object_index=payload.object_index,
                    property_id=payload.property_id + (failure == "metadata"),
                    start_index=payload.start_index,
                    count=1,
                    data={
                        "empty": b"",
                        "truncated": b"a",
                        "metadata": b"ab",
                        "pid7": b"abc",
                    }[failure],
                )
            )

    app, _ = seeded_application()
    control = LdCtrlCompareProp(
        obj_idx=0,
        prop_id=7 if failure == "pid7" else 13,
        inline_data=b"ab",
        on_error=[IGNORE],
    )
    runner = LoadProcedureRunner(
        app,
        DownloadImage((), ()),
        DeviceProgrammer(InvalidDevice()),
        controls=[control],
    )
    with pytest.raises((VerificationError, TimeoutError)) as error:
        await (runner.preflight() if preview else runner.run())
    assert not isinstance(error.value, CompareMismatch)


@pytest.mark.parametrize("preview", [False, True])
@pytest.mark.parametrize("nested", ["none", "match", "wrong-cause", "fail"])
async def test_nested_handlers_take_precedence_over_legacy_mapping(preview, nested):
    class AbsentProperty(FakeDevice):
        async def request(self, payload, expected):
            assert isinstance(payload, PropertyValueRead)
            return self._telegram(
                PropertyValueResponse(
                    object_index=0,
                    property_id=13,
                    start_index=payload.start_index,
                    count=0,
                    data=b"",
                )
            )

    handlers = {
        "none": [],
        "match": [
            LdCtrlBaseOnError(cause=LdCtrlErrorCause.RESOURCE_NOT_FOUND, ignore=True)
        ],
        "wrong-cause": [IGNORE],
        "fail": [LdCtrlBaseOnError(cause=LdCtrlErrorCause.RESOURCE_NOT_FOUND)],
    }[nested]
    app, _ = seeded_application()
    runner = LoadProcedureRunner(
        app,
        DownloadImage((), ()),
        DeviceProgrammer(AbsentProperty()),
        controls=[
            LdCtrlMapError(original_error=0xC0042B07, mapped_error=0),
            LdCtrlCompareProp(
                obj_idx=0, prop_id=13, inline_data=b"x", on_error=handlers
            ),
        ],
    )
    if nested in ("none", "match"):
        await (runner.preflight() if preview else runner.run())
    else:
        with pytest.raises(PropertyAccessRejected):
            await (runner.preflight() if preview else runner.run())


@pytest.mark.parametrize("message_exists", [False, True])
async def test_message_ref_replaces_message_preserving_failure(message_exists):
    app, _ = seeded_application()
    app.program.static.messages = ApplicationProgramStaticMessages(
        message=[
            ApplicationProgramStaticMessagesMessage(
                id="message", name="message", text="Application is incompatible"
            )
        ]
        if message_exists
        else []
    )
    device = FakeDevice()
    device.properties[0, 13] = b"y"
    control = LdCtrlCompareProp(
        obj_idx=0,
        prop_id=13,
        inline_data=b"x",
        on_error=[
            LdCtrlBaseOnError(
                cause=LdCtrlErrorCause.COMPARE_MISMATCH, message_ref="message"
            )
        ],
    )
    runner = LoadProcedureRunner(
        app, DownloadImage((), ()), DeviceProgrammer(device), controls=[control]
    )
    with pytest.raises(CompareMismatch) as error:
        await runner.run()
    assert error.value.error_code == 0xC0042B09
    if message_exists:
        assert str(error.value) == (
            "Application is incompatible The device reports application version 79, "
            "the application expects 78."
        )
        assert isinstance(error.value.__cause__, CompareMismatch)
    else:
        assert "property compare failed" in str(error.value)
    assert error.value.read == b"y"
    assert error.value.expected == b"x"


async def test_compare_mismatch_reports_the_masked_hardware_type():
    app, _ = seeded_application()
    device = FakeDevice()
    device.properties[0, 78] = bytes.fromhex("000000140001")
    control = LdCtrlCompareProp(
        obj_idx=0,
        prop_id=78,
        inline_data=bytes.fromhex("000000250000"),
        mask=bytes.fromhex("FFFFFFFF0000"),
    )
    runner = LoadProcedureRunner(
        app, DownloadImage((), ()), DeviceProgrammer(device), controls=[control]
    )
    with pytest.raises(CompareMismatch) as error:
        await runner.run()
    assert error.value.details == (
        "The device reports hardware type 00 00 00 14 00 01, "
        "the application expects 00 00 00 25 xx xx."
    )
