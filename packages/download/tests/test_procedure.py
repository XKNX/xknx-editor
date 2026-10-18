"""Tests for the Load Procedure interpreter."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from xknx.telegram.apci import (
    FunctionPropertyCommand,
    FunctionPropertyStateRead,
    MemoryRead,
    MemoryWrite,
    PropertyValueRead,
    PropertyValueWrite,
)

from xknxeditor.download.errors import DownloadError, PropertyAccessRejected
from xknxeditor.download.image import (
    DownloadImage,
    MemorySegment,
    PropertyValue,
    RelativeSegment,
)
from xknxeditor.download.load_state import (
    PID_LOAD_STATE_CONTROL,
    LoadEvent,
    LoadState,
    start_loading,
)
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.scope import DownloadScope
from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_clear_lcfilter_table_t import (
    LdCtrlClearLcfilterTable,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_connect_t import LdCtrlConnect
from xknxeditor.namespaces.intermediate.ld_ctrl_declare_prop_desc_t import (
    LdCtrlDeclarePropDesc,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_disconnect_t import LdCtrlDisconnect
from xknxeditor.namespaces.intermediate.ld_ctrl_invoke_function_prop_t import (
    LdCtrlInvokeFunctionProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_completed_t import (
    LdCtrlLoadCompleted,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_image_mem_t import (
    LdCtrlLoadImageMem,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_map_error_t import LdCtrlMapError
from xknxeditor.namespaces.intermediate.ld_ctrl_master_reset_t import LdCtrlMasterReset
from xknxeditor.namespaces.intermediate.ld_ctrl_read_function_prop_t import (
    LdCtrlReadFunctionProp,
)
from xknxeditor.namespaces.intermediate.ld_ctrl_rel_segment_t import LdCtrlRelSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_restart_t import LdCtrlRestart
from xknxeditor.namespaces.intermediate.ld_ctrl_task_segment_t import LdCtrlTaskSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_unload_t import LdCtrlUnload
from xknxeditor.namespaces.intermediate.ld_ctrl_write_mem_t import LdCtrlWriteMem
from xknxeditor.namespaces.intermediate.ld_ctrl_write_prop_t import LdCtrlWriteProp
from xknxeditor.namespaces.intermediate.ld_ctrl_write_rel_mem_t import LdCtrlWriteRelMem
from xknxeditor.namespaces.intermediate.load_procedure_style_t import LoadProcedureStyle
from xknxeditor.namespaces.intermediate.load_procedures_t import LoadProcedures
from xknxeditor.namespaces.intermediate.load_procedures_t_load_procedure import (
    LoadProceduresLoadProcedure,
)

from .conftest import FakeDevice

if True:  # keep import used for typing without a runtime dependency cycle
    from xknxeditor.prod import Application

# Interface object type of the address table object used in the fixtures below.
_ADDRESS_TABLE_TYPE = 1


def _application(*controls: object) -> Application:
    """Wrap Load Controls into a minimal application stand-in."""
    procedure = LoadProceduresLoadProcedure(choice=list(controls))  # type: ignore[arg-type]
    load_procedures = LoadProcedures(load_procedure=[procedure])
    fake = SimpleNamespace(
        load_procedures=load_procedures,
        load_procedure_style=LoadProcedureStyle.PRODUCT_PROCEDURE,
        manufacturer_id="M-0072",
        program=SimpleNamespace(
            pei_type=1,
            application_number=1,
            application_version=1,
            mask_version="MV-07B0",
        ),
    )
    return cast("Application", fake)


def _device(object_types: dict[int, int] | None = None) -> FakeDevice:
    """System B test device matching ``_application``'s MV-07B0 mask."""
    types = (
        object_types
        if object_types is not None
        else {0: 0x0000, 1: _ADDRESS_TABLE_TYPE}
    )
    return FakeDevice(object_types=types, descriptor=0x07B0)


def _runner(
    application: Application, image: DownloadImage
) -> tuple[LoadProcedureRunner, FakeDevice]:
    device = _device()
    programmer = DeviceProgrammer(device)
    return LoadProcedureRunner(application, image, programmer), device


async def test_full_property_based_sequence() -> None:
    image = DownloadImage(
        segments=(MemorySegment(address=0x4000, data=bytes(range(8))),),
        properties=(),
    )
    application = _application(
        LdCtrlConnect(),
        LdCtrlUnload(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
        LdCtrlLoad(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
        LdCtrlWriteMem(
            address=0x4000, size=8, verify=False, inline_data=bytes(range(8))
        ),
        LdCtrlLoadCompleted(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
        LdCtrlRestart(),
        LdCtrlDisconnect(),
    )
    runner, device = _runner(application, image)

    await runner.run()

    # the data was written to memory
    assert bytes(device.memory[0x4000 + i] for i in range(8)) == bytes(range(8))
    # the load state machine ended up loaded and the device restarted
    assert device.load_states[1] == LoadState.LOADED
    assert device.restarted
    assert runner.restarted


async def test_load_stays_loaded_after_start_loading() -> None:
    # Present DIMinBOX object 1 accepts START_LOADING but keeps reporting LOADED;
    # this is separate from the optional, absent object 5 MapError tolerance.
    class StaysLoadedDevice(FakeDevice):
        def _handle_property_write(self, payload: PropertyValueWrite) -> None:
            if (
                payload.object_index == 1
                and payload.property_id == PID_LOAD_STATE_CONTROL
                and payload.data == start_loading()
            ):
                return
            super()._handle_property_write(payload)

    device = StaysLoadedDevice(object_types={0: 0, 1: _ADDRESS_TABLE_TYPE})
    device.load_states[1] = LoadState.LOADED
    application = _application(
        LdCtrlLoad(lsm_idx=1),
        LdCtrlWriteMem(address=0x4000, size=2, inline_data=b"\x11\x22", verify=True),
        LdCtrlLoadCompleted(lsm_idx=1),
    )
    programmer = DeviceProgrammer(device)
    runner = LoadProcedureRunner(application, _empty_image(), programmer)

    await runner.run()

    assert await programmer.read_load_state(1) == LoadState.LOADED
    assert bytes(device.memory[0x4000 + i] for i in range(2)) == b"\x11\x22"
    assert [
        p.data[0]
        for p in device.sent
        if isinstance(p, PropertyValueWrite)
        and p.object_index == 1
        and p.property_id == PID_LOAD_STATE_CONTROL
    ] == [LoadEvent.START_LOADING, LoadEvent.LOAD_COMPLETE]
    # The only state read is our explicit check above: no 30-second poll loop.
    assert (
        sum(
            isinstance(p, PropertyValueRead) and p.property_id == PID_LOAD_STATE_CONTROL
            for p in device.sent
        )
        == 1
    )


async def test_load_image_mem_reads_without_writing() -> None:
    application = _application(LdCtrlLoadImageMem(address=0x4000, size=4))
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))

    await runner.run()

    # LoadImageMem reads device memory into the image; it must not write.
    assert 0x4000 not in device.memory
    assert any(isinstance(p, MemoryRead) and p.address == 0x4000 for p in device.sent)


async def test_state_mutated_stays_false_for_read_only_run() -> None:
    application = _application(
        LdCtrlConnect(),
        LdCtrlLoadImageMem(address=0x4000, size=4),
        LdCtrlDisconnect(),
    )
    runner, _ = _runner(application, DownloadImage(segments=(), properties=()))

    await runner.run()

    assert runner.state_mutated is False


async def test_state_mutated_set_once_a_write_runs() -> None:
    image = DownloadImage(
        segments=(MemorySegment(address=0x4000, data=bytes(range(8))),),
        properties=(),
    )
    application = _application(
        LdCtrlConnect(),
        LdCtrlUnload(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
        LdCtrlLoad(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
        LdCtrlWriteMem(
            address=0x4000, size=8, verify=False, inline_data=bytes(range(8))
        ),
        LdCtrlLoadCompleted(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
        LdCtrlDisconnect(),
    )
    runner, _ = _runner(application, image)

    await runner.run()

    assert runner.state_mutated is True

    application = _application(
        LdCtrlConnect(),
        LdCtrlMasterReset(erase_code=4, channel_number=0),
        LdCtrlDisconnect(),
    )
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))

    await runner.run()

    # The Master Reset went out with its erase code / channel and marked a restart.
    assert device.master_reset == (4, 0)
    assert device.restarted
    assert runner.restarted


async def test_master_reset_error_code_raises() -> None:
    application = _application(LdCtrlMasterReset(erase_code=4, channel_number=0))
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))
    device.master_reset_error_code = 0x02

    with pytest.raises(DownloadError, match="refused master reset"):
        await runner.run()


async def test_invoke_function_property_sends_command() -> None:
    application = _application(
        LdCtrlInvokeFunctionProp(obj_idx=1, prop_id=52, inline_data=bytes([0x01, 0x02]))
    )
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))

    await runner.run()

    assert device.function_properties[(1, 52)] == bytes([0x01, 0x02])
    assert any(
        isinstance(p, FunctionPropertyCommand)
        and p.object_index == 1
        and p.property_id == 52
        for p in device.sent
    )


async def test_invoke_function_property_error_code_raises() -> None:
    application = _application(
        LdCtrlInvokeFunctionProp(obj_idx=1, prop_id=52, inline_data=b"\x01")
    )
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))
    device.function_property_return_code = 0x03

    with pytest.raises(DownloadError, match="returned error code"):
        await runner.run()


async def test_read_function_property_reads_without_writing() -> None:
    application = _application(LdCtrlReadFunctionProp(obj_idx=1, prop_id=52))
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))

    await runner.run()

    assert (1, 52) not in device.function_properties
    assert any(
        isinstance(p, FunctionPropertyStateRead) and p.property_id == 52
        for p in device.sent
    )


async def test_declare_prop_desc_is_client_side_noop() -> None:
    # DeclarePropDesc only declares a property description to the client object
    # model; it must not send any telegram.
    application = _application(
        LdCtrlDeclarePropDesc(
            obj_idx=1,
            prop_id=52,
            prop_type=1,
            max_elements=1,
            read_access=0,
            write_access=0,
            writable=True,
        )
    )
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))

    await runner.run()

    assert device.sent == []


async def test_write_mem_inline_data() -> None:
    application = _application(
        LdCtrlWriteMem(address=0x100, size=3, verify=False, inline_data=b"\x01\x02\x03")
    )
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))

    await runner.run()

    assert bytes(device.memory[0x100 + i] for i in range(3)) == b"\x01\x02\x03"


async def test_write_mem_image_backed() -> None:
    image = DownloadImage(
        segments=(MemorySegment(address=0x500, data=b"\xde\xad\xbe\xef"),),
        properties=(),
    )
    application = _application(
        LdCtrlWriteMem(address=0x500, size=4, verify=False, inline_data=None)
    )
    runner, device = _runner(application, image)

    await runner.run()

    assert bytes(device.memory[0x500 + i] for i in range(4)) == b"\xde\xad\xbe\xef"


async def test_write_rel_mem_uses_table_reference() -> None:
    application = _application(
        LdCtrlWriteRelMem(
            obj_type=3,
            occurrence=0,
            offset=4,
            size=2,
            verify=False,
            inline_data=b"\x11\x22",
        )
    )
    device = _device({0: 0x0000, 1: 3})
    device.table_references[1] = 0x4000
    runner = LoadProcedureRunner(
        application, DownloadImage(segments=(), properties=()), DeviceProgrammer(device)
    )

    await runner.run()

    # written at table base (0x4000) + offset (4)
    assert bytes(device.memory[0x4004 + i] for i in range(2)) == b"\x11\x22"


async def test_write_rel_mem_image_backed_uses_relative_lookup() -> None:
    # The image mirrors a relative segment in its own relative address space
    # (base 0x0); the device write adds the table base read at run time. Only the
    # masked bytes are written, at base + their relative offset.
    image = DownloadImage(
        segments=(
            MemorySegment(
                address=0x0,
                data=b"\x00\x00\xab\xcd",
                mask=b"\x00\x00\xff\xff",
            ),
        ),
        properties=(),
    )
    application = _application(
        LdCtrlWriteRelMem(
            obj_type=3,
            occurrence=0,
            offset=0,
            size=4,
            verify=False,
            inline_data=None,
        )
    )
    device = _device({0: 0x0000, 1: 3})
    device.table_references[1] = 0x3804
    runner = LoadProcedureRunner(application, image, DeviceProgrammer(device))

    await runner.run()

    # relative offsets 2..3 written at table base 0x3804 + 2
    assert bytes(device.memory[0x3806 + i] for i in range(2)) == b"\xab\xcd"
    assert 0x3804 not in device.memory and 0x3805 not in device.memory


async def test_write_rel_mem_relative_segment_by_object_type() -> None:
    # A System B group communication table: the image holds it as a relative
    # segment keyed by object type, and the write lands at the table base.
    image = DownloadImage(
        segments=(),
        properties=(),
        relative_segments=(
            RelativeSegment(
                object_type=_ADDRESS_TABLE_TYPE,
                data=b"\x00\x02\x0b\x08",
                mask=b"\xff\xff\xff\xff",
            ),
        ),
    )
    application = _application(
        LdCtrlLoad(obj_type=1),
        LdCtrlRelSegment(obj_type=1, size=2, mode=0, fill=0),
        LdCtrlWriteRelMem(
            obj_type=_ADDRESS_TABLE_TYPE,
            occurrence=0,
            offset=0,
            size=4,
            verify=False,
            inline_data=None,
        ),
        LdCtrlLoadCompleted(obj_type=1),
    )
    device = _device({0: 0x0000, 1: _ADDRESS_TABLE_TYPE})
    device.table_references[1] = 0x3400
    runner = LoadProcedureRunner(application, image, DeviceProgrammer(device))

    await runner.run()

    assert bytes(device.memory[0x3400 + i] for i in range(4)) == b"\x00\x02\x0b\x08"


async def test_coupler_filter_table_clear_is_noop_and_write_lands() -> None:
    # A coupler load procedure clears the filter table then writes the full table into the Router
    # object (object type 6) relative memory. The clear is a no-op (the full write follows); the
    # WriteRelMem writes the supplied filter-table bitmap at the object's table base.
    router = 6
    table = bytes([0b1000_0001, 0x00, 0xFF])
    image = DownloadImage(
        segments=(),
        properties=(),
        relative_segments=(RelativeSegment(object_type=router, data=table),),
    )
    application = _application(
        LdCtrlConnect(),
        LdCtrlClearLcfilterTable(use_function_prop=True),
        LdCtrlWriteRelMem(
            obj_type=router,
            occurrence=0,
            offset=0,
            size=len(table),
            verify=False,
            inline_data=None,
        ),
        LdCtrlDisconnect(),
    )
    # Router object (type 6) at interface object index 2; its table base is where the write lands.
    device = _device({0: 0x0000, 1: _ADDRESS_TABLE_TYPE, 2: router})
    device.table_references[2] = 0x5000
    runner = LoadProcedureRunner(application, image, DeviceProgrammer(device))

    await runner.run()

    assert bytes(device.memory[0x5000 + i] for i in range(len(table))) == table


async def test_bcu1_lcfilter_and_lcslave_writes_are_absolute() -> None:
    from xknxeditor.namespaces.intermediate.ld_ctrl_mem_addr_space_t import (
        LdCtrlMemAddrSpace,
    )

    # BCU1 coupler: LcFilter and LcSlave use flat absolute A_Memory_Writes.
    # MV-0900 writes LcSlave config (e.g. @270) then the LcFilter
    # table (@512); both must land at their absolute address.
    table = bytes([0b1010_1010, 0x00, 0xFF])
    app = _application(
        LdCtrlConnect(),
        LdCtrlWriteMem(
            address=270,
            size=1,
            verify=False,
            inline_data=b"\x01",
            address_space=LdCtrlMemAddrSpace.LC_SLAVE,
        ),
        LdCtrlWriteMem(
            address=512,
            size=len(table),
            verify=False,
            inline_data=table,
            address_space=LdCtrlMemAddrSpace.LC_FILTER,
        ),
        LdCtrlDisconnect(),
    )
    runner, device = _runner(app, DownloadImage(segments=(), properties=()))
    await runner.run()
    assert device.memory[270] == 0x01
    assert bytes(device.memory[512 + i] for i in range(len(table))) == table


async def test_filter_table_field_feeds_systemb_and_bcu1() -> None:
    from xknxeditor.namespaces.intermediate.ld_ctrl_mem_addr_space_t import (
        LdCtrlMemAddrSpace,
    )

    table = bytes([0b0000_1111, 0xAA, 0x00, 0x55])
    # System B: WriteRelMem on the Router object (type 6) pulls the image's filter_table field.
    image = DownloadImage(segments=(), properties=(), filter_table=table)
    app = _application(
        LdCtrlConnect(),
        LdCtrlClearLcfilterTable(use_function_prop=True),
        LdCtrlWriteRelMem(
            obj_type=6,
            occurrence=0,
            offset=0,
            size=len(table),
            verify=False,
            inline_data=None,
        ),
        LdCtrlDisconnect(),
    )
    device = _device({0: 0x0000, 1: _ADDRESS_TABLE_TYPE, 2: 6})
    device.table_references[2] = 0x4200
    await LoadProcedureRunner(app, image, DeviceProgrammer(device)).run()
    assert bytes(device.memory[0x4200 + i] for i in range(len(table))) == table

    # BCU1: WriteMem in the LcFilter absolute space also pulls the filter_table field.
    app2 = _application(
        LdCtrlConnect(),
        LdCtrlWriteMem(
            address=512,
            size=len(table),
            verify=False,
            inline_data=None,
            address_space=LdCtrlMemAddrSpace.LC_FILTER,
        ),
        LdCtrlDisconnect(),
    )
    runner2, device2 = _runner(app2, image)
    await runner2.run()
    assert bytes(device2.memory[512 + i] for i in range(len(table))) == table


async def test_clear_lcfilter_table_alone_is_noop() -> None:
    # The clear control on its own must run without touching the bus (no unsupported-control error).
    application = _application(
        LdCtrlConnect(),
        LdCtrlClearLcfilterTable(use_function_prop=False),
        LdCtrlDisconnect(),
    )
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))
    await runner.run()
    assert device.memory == {}


def _compare_mem_ctrl(
    address: int, inline_data: bytes, *, mask: bytes | None = None, **extra: object
):
    from xknxeditor.namespaces.intermediate.ld_ctrl_compare_mem_t import (
        LdCtrlCompareMem,
    )
    from xknxeditor.namespaces.intermediate.ld_ctrl_mem_addr_space_t import (
        LdCtrlMemAddrSpace,
    )

    return LdCtrlCompareMem(
        address=address,
        size=len(inline_data),
        inline_data=inline_data,
        mask=mask,
        address_space=LdCtrlMemAddrSpace.STANDARD,
        **extra,
    )


async def test_read_memory_backs_off_on_zero_length_reply() -> None:
    # BIM M112 / mask 0701 quirk: a too-large A_Memory_Read answers with an empty MemoryResponse
    # while smaller counts serve data. read_memory must shrink the block and retry, not abort with
    # "no memory response". (Covers Test/preflight, verify read-backs and Compare controls.)
    device = FakeDevice()
    device.memory_read_zero_above = 8
    for i in range(40):
        device.memory[0x4808 + i] = i & 0xFF
    programmer = DeviceProgrammer(device)
    data = await programmer.read_memory(0x4808, 40)
    assert data == bytes(i & 0xFF for i in range(40))


async def test_compare_mem_equality() -> None:
    from xknxeditor.download.errors import VerificationError

    # Equal -> pass.
    app = _application(
        LdCtrlConnect(), _compare_mem_ctrl(0x100, b"\x12\x34"), LdCtrlDisconnect()
    )
    runner, device = _runner(app, DownloadImage(segments=(), properties=()))
    device.memory[0x100], device.memory[0x101] = 0x12, 0x34
    await runner.run()

    # Different -> VerificationError.
    app2 = _application(
        LdCtrlConnect(), _compare_mem_ctrl(0x100, b"\x12\xff"), LdCtrlDisconnect()
    )
    runner2, device2 = _runner(app2, DownloadImage(segments=(), properties=()))
    device2.memory[0x100], device2.memory[0x101] = 0x12, 0x34
    with pytest.raises(VerificationError):
        await runner2.run()


async def test_compare_mem_mask() -> None:
    from xknxeditor.download.errors import VerificationError

    # Only the high nibble is compared: a difference in the masked-out low nibble still passes.
    app = _application(
        LdCtrlConnect(),
        _compare_mem_ctrl(0x100, b"\xff", mask=b"\xf0"),
        LdCtrlDisconnect(),
    )
    runner, device = _runner(app, DownloadImage(segments=(), properties=()))
    device.memory[0x100] = 0xF7  # high nibble matches expected 0xF*, low nibble differs
    await runner.run()

    # A difference in a masked (compared) bit fails.
    app2 = _application(
        LdCtrlConnect(),
        _compare_mem_ctrl(0x100, b"\xff", mask=b"\xf0"),
        LdCtrlDisconnect(),
    )
    runner2, device2 = _runner(app2, DownloadImage(segments=(), properties=()))
    device2.memory[0x100] = 0x7F  # high nibble 0x7 != expected 0xF
    with pytest.raises(VerificationError):
        await runner2.run()


async def test_compare_mem_invert_and_retry_rejected_upfront() -> None:
    from xknxeditor.download.errors import UnsupportedProcedureError

    # Invert / RetryInterval semantics are not implemented; a compare using them must be rejected
    # in prevalidation (before any bus access), not silently mis-verified.
    for extra in ({"invert": True}, {"retry_interval": 100, "time_out": 1000}):
        app = _application(
            LdCtrlConnect(),
            _compare_mem_ctrl(0x100, b"\x00", **extra),
            LdCtrlDisconnect(),
        )
        runner, device = _runner(app, DownloadImage(segments=(), properties=()))
        device.memory[0x100] = 0x00
        with pytest.raises(UnsupportedProcedureError):
            await runner.run()
        assert device.memory.get(0x100) == 0x00  # untouched; rejected up front


async def test_write_prop_inline_data() -> None:
    application = _application(
        LdCtrlWriteProp(
            obj_idx=5,
            prop_id=0x33,
            start_element=1,
            count=1,
            verify=False,
            inline_data=b"\xaa\xbb",
        )
    )
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))

    await runner.run()

    writes = [p for p in device.sent if isinstance(p, PropertyValueWrite)]
    assert writes and writes[0].object_index == 5
    assert device.properties[(5, 0x33)] == b"\xaa\xbb"


async def test_write_prop_image_backed() -> None:
    image = DownloadImage(
        segments=(),
        properties=(
            PropertyValue(
                object_index=5, property_id=0x33, occurrence=0, data=b"\xca\xfe"
            ),
        ),
    )
    application = _application(
        LdCtrlWriteProp(
            obj_idx=5,
            prop_id=0x33,
            start_element=1,
            count=1,
            verify=False,
            inline_data=None,
        )
    )
    runner, device = _runner(application, image)

    await runner.run()

    assert device.properties[(5, 0x33)] == b"\xca\xfe"


async def test_compare_prop_tolerates_trailing_padding() -> None:
    from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import (
        LdCtrlCompareProp,
    )

    device = FakeDevice()
    device.properties[(0, 78)] = (
        b"\x00\x00\x00\x00\x02\x27"  # 6 bytes, as a real device reports
    )
    application = _application(
        LdCtrlCompareProp(
            obj_idx=0,
            prop_id=78,
            start_element=1,
            count=1,
            inline_data=b"\x00\x00\x00\x00\x02\x27\x00\x00\x00\x00",  # padded to 10
        )
    )
    runner = LoadProcedureRunner(
        application, DownloadImage(segments=(), properties=()), DeviceProgrammer(device)
    )
    await runner.run()  # must not raise: the 6 significant bytes match


async def test_compare_prop_detects_real_mismatch() -> None:
    from xknxeditor.download.errors import VerificationError
    from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import (
        LdCtrlCompareProp,
    )

    device = FakeDevice()
    device.properties[(0, 78)] = b"\x00\x00\x00\x00\x09\x99"
    application = _application(
        LdCtrlCompareProp(
            obj_idx=0,
            prop_id=78,
            start_element=1,
            count=1,
            inline_data=b"\x00\x00\x00\x00\x02\x27",
        )
    )
    runner = LoadProcedureRunner(
        application, DownloadImage(segments=(), properties=()), DeviceProgrammer(device)
    )
    with pytest.raises(VerificationError, match="compare failed"):
        await runner.run()


async def test_compare_prop_mask_ignores_unmarked_octets() -> None:
    from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import (
        LdCtrlCompareProp,
    )

    device = FakeDevice()
    # application id: manufacturer 0x0002, app number 0xa062, version 0x14
    device.properties[(0, 13)] = b"\x00\x02\xa0\x62\x14"
    application = _application(
        LdCtrlCompareProp(
            obj_idx=0,
            prop_id=13,
            start_element=1,
            count=1,
            inline_data=b"\x00\x00\xa0\x62\x00",
            mask=b"\x00\x00\xff\xff\x00",  # only the app-number octets matter
        )
    )
    runner = LoadProcedureRunner(
        application, DownloadImage(segments=(), properties=()), DeviceProgrammer(device)
    )
    await runner.run()  # must not raise: manufacturer and version are masked out


async def test_compare_prop_mask_still_detects_marked_mismatch() -> None:
    from xknxeditor.download.errors import VerificationError
    from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import (
        LdCtrlCompareProp,
    )

    device = FakeDevice()
    device.properties[(0, 13)] = b"\x00\x02\xa0\x63\x14"  # app number differs
    application = _application(
        LdCtrlCompareProp(
            obj_idx=0,
            prop_id=13,
            start_element=1,
            count=1,
            inline_data=b"\x00\x00\xa0\x62\x00",
            mask=b"\x00\x00\xff\xff\x00",
        )
    )
    runner = LoadProcedureRunner(
        application, DownloadImage(segments=(), properties=()), DeviceProgrammer(device)
    )
    with pytest.raises(VerificationError, match="compare failed"):
        await runner.run()


async def test_abs_segment_writes_image_data_for_its_range() -> None:
    image = DownloadImage(
        segments=(MemorySegment(address=0x4000, data=b"\x01\x02\x03\x04"),),
        properties=(),
    )
    application = _application(
        LdCtrlAbsSegment(
            obj_type=_ADDRESS_TABLE_TYPE,
            occurrence=0,
            seg_type=0,
            address=0x4000,
            size=4,
            access=0xFF,
            mem_type=3,
            seg_flags=0x80,
        )
    )
    runner, device = _runner(application, image)

    await runner.run()

    # segment allocated (LSM loading) and its image data written to memory
    assert device.load_states[1] == LoadState.LOADING
    assert bytes(device.memory[0x4000 + i] for i in range(4)) == b"\x01\x02\x03\x04"


async def test_abs_segment_without_image_only_allocates() -> None:
    application = _application(
        LdCtrlAbsSegment(
            obj_type=_ADDRESS_TABLE_TYPE,
            occurrence=0,
            seg_type=0,
            address=0x9000,
            size=4,
            access=0xFF,
            mem_type=3,
            seg_flags=0x80,
        )
    )
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))

    await runner.run()

    assert device.load_states[1] == LoadState.LOADING
    assert 0x9000 not in device.memory  # nothing written when image lacks the range


async def test_load_event_written_to_load_state_control() -> None:
    application = _application(LdCtrlLoad(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0))
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))

    await runner.run()

    load_events = [
        p
        for p in device.sent
        if isinstance(p, PropertyValueWrite) and p.property_id == PID_LOAD_STATE_CONTROL
    ]
    assert load_events and load_events[0].data[0] == 0x01  # start loading


async def test_lsm_idx_addresses_object_directly() -> None:
    # For property based management LsmIdx is the interface object index.
    application = _application(LdCtrlLoad(lsm_idx=1, occurrence=0))
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))

    await runner.run()

    assert device.load_states[1] == LoadState.LOADING


async def test_connect_and_disconnect_are_noops() -> None:
    application = _application(LdCtrlConnect(), LdCtrlDisconnect())
    runner, device = _runner(application, DownloadImage(segments=(), properties=()))

    await runner.run()

    assert device.sent == []


_ASSOCIATION_TABLE_TYPE = 2
_APPLICATION_TYPE = 3


def _scoped_application() -> Application:
    # group communication parts (address=1, association=2) and the application
    # part (3); automatic load controls follow their per-type rules.
    return _application(
        LdCtrlLoad(obj_type=1, occurrence=0),
        LdCtrlLoad(obj_type=_ASSOCIATION_TABLE_TYPE, occurrence=0),
        LdCtrlLoad(obj_type=_APPLICATION_TYPE, occurrence=0),
    )


def _scoped_device() -> FakeDevice:
    return FakeDevice(object_types={0: 0x0000, 1: 1, 2: 2, 3: 3})


async def test_auto_load_controls_in_parameter_scope() -> None:
    device = _scoped_device()
    runner = LoadProcedureRunner(
        _scoped_application(),
        DownloadImage(segments=(), properties=()),
        DeviceProgrammer(device),
        scope=DownloadScope.PARAMETERS,
    )
    await runner.run()
    assert set(device.load_states) == {3}


async def test_auto_load_controls_in_group_scope() -> None:
    device = _scoped_device()
    runner = LoadProcedureRunner(
        _scoped_application(),
        DownloadImage(segments=(), properties=()),
        DeviceProgrammer(device),
        scope=DownloadScope.GROUP_COMMUNICATION,
    )
    await runner.run()
    assert set(device.load_states) == {1, 2, 3}


async def test_full_scope_runs_all_parts() -> None:
    device = _scoped_device()
    runner = LoadProcedureRunner(
        _scoped_application(),
        DownloadImage(segments=(), properties=()),
        DeviceProgrammer(device),
        scope=DownloadScope.FULL,
    )
    await runner.run()
    assert set(device.load_states) == {1, 2, 3}


class _FakeManager:
    """Connection manager handing out one fake device, counting open/close."""

    def __init__(self, device: FakeDevice) -> None:
        self.device = device
        self.opens = 0
        self.closes = 0

    async def open(self) -> FakeDevice:
        self.opens += 1
        return self.device

    async def close(self) -> None:
        self.closes += 1


def _managed_runner(
    *controls: object, **kwargs: object
) -> tuple[LoadProcedureRunner, _FakeManager]:
    device = FakeDevice(object_types={0: 0x0000, 1: _ADDRESS_TABLE_TYPE})
    manager = _FakeManager(device)
    runner = LoadProcedureRunner(
        _application(*controls),
        DownloadImage(segments=(), properties=()),
        connection_manager=manager,
        restart_cooldown=0,
        **kwargs,  # type: ignore[arg-type]
    )
    return runner, manager


async def test_connect_opens_and_disconnect_closes() -> None:
    runner, manager = _managed_runner(
        LdCtrlConnect(),
        LdCtrlLoad(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
        LdCtrlDisconnect(),
    )
    await runner.run()
    assert manager.opens == 1
    assert manager.closes == 1
    assert manager.device.load_states[1] == LoadState.LOADING


async def test_bus_control_auto_connects() -> None:
    runner, manager = _managed_runner(
        LdCtrlLoad(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0)
    )
    await runner.run()
    assert manager.opens == 1  # opened automatically, no explicit Connect


async def test_restart_tears_down_and_next_control_reconnects() -> None:
    runner, manager = _managed_runner(
        LdCtrlConnect(),
        LdCtrlRestart(),
        LdCtrlLoad(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
    )
    await runner.run()
    assert runner.restarted
    assert manager.device.restarted
    # opened for Connect, torn down by Restart, reopened for Load
    assert manager.opens == 2
    assert manager.closes >= 1


async def test_runner_requires_programmer_or_manager() -> None:
    with pytest.raises(DownloadError, match="programmer or a connection manager"):
        LoadProcedureRunner(_application(), DownloadImage(segments=(), properties=()))


async def test_expected_descriptor_mismatch_refuses_to_program() -> None:
    # Device reports mask 0x0705 (FakeDevice default); expecting System B 0x07B0.
    runner, manager = _managed_runner(
        LdCtrlLoad(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
        expected_descriptor=0x07B0,
    )
    with pytest.raises(DownloadError, match="device mask mismatch"):
        await runner.run()
    # It refused before driving the load state machine.
    assert manager.device.load_states == {}


async def test_expected_descriptor_match_proceeds() -> None:
    runner, manager = _managed_runner(
        LdCtrlLoad(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
        expected_descriptor=0x0705,
    )
    await runner.run()
    assert manager.device.load_states[1] == LoadState.LOADING


async def test_authorize_runs_when_levels_positive() -> None:
    runner, manager = _managed_runner(
        LdCtrlLoad(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
        authorize_levels=4,
    )
    await runner.run()
    # A device with access protection is authorized once on connect, with the
    # free access key.
    assert manager.device.authorize_keys == [0xFFFFFFFF]


async def test_authorize_skipped_when_levels_zero() -> None:
    runner, manager = _managed_runner(
        LdCtrlLoad(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
        authorize_levels=0,
    )
    await runner.run()
    assert manager.device.authorize_keys == []


async def test_bcu1_skips_property_probes() -> None:
    runner, manager = _managed_runner(
        LdCtrlConnect(),
        LdCtrlWriteMem(address=0x10D, size=1, verify=True, inline_data=b"\x00"),
        max_apdu_length=239,
        negotiate_apdu=True,
        property_services=False,
    )

    await runner.run()

    assert not any(type(p).__name__ == "PropertyValueRead" for p in manager.device.sent)
    assert manager.device.memory[0x10D] == 0
    assert any(
        type(p).__name__ == "MemoryRead" and getattr(p, "address", None) == 0x10D
        for p in manager.device.sent
    )
    assert runner._programmer is not None
    assert runner._programmer.max_apdu_length == 15


async def test_negotiate_apdu_uses_device_maximum() -> None:
    runner, manager = _managed_runner(
        LdCtrlLoad(obj_type=_ADDRESS_TABLE_TYPE, occurrence=0),
        max_apdu_length=254,
        negotiate_apdu=True,
    )
    # Device Object PID_MAX_APDU_LENGTH (56) reports 40 octets.
    manager.device.properties[(0, 56)] = (40).to_bytes(2, "big")

    await runner.run()

    # The device's maximum APDU length was read (negotiated) before programming.
    assert any(
        type(p).__name__ == "PropertyValueRead"
        and getattr(p, "object_index", None) == 0
        and getattr(p, "property_id", None) == 56
        for p in manager.device.sent
    )


async def test_compare_prop_rejects_a_truncated_response() -> None:
    """A short reply must not shrink the compare onto a matching prefix.

    The compare is the gate that stops a download reaching the wrong device, so a device that
    answers one octet of a five octet application id has not passed it - it has failed to answer.
    """
    from xknxeditor.download.errors import VerificationError
    from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import (
        LdCtrlCompareProp,
    )

    device = FakeDevice()
    device.properties[(0, 13)] = b"\x00"  # truncated: 1 of 5 octets, and it matches
    application = _application(
        LdCtrlCompareProp(
            obj_idx=0,
            prop_id=13,
            start_element=1,
            count=1,
            inline_data=b"\x00\x02\xa0\x62\x14",
        )
    )
    runner = LoadProcedureRunner(
        application, DownloadImage(segments=(), properties=()), DeviceProgrammer(device)
    )
    with pytest.raises(VerificationError, match="beyond the response"):
        await runner.run()


async def test_compare_prop_truncation_respects_the_mask() -> None:
    """Octets past the reply that the mask excludes are not data the compare was checking."""
    from xknxeditor.namespaces.intermediate.ld_ctrl_compare_prop_t import (
        LdCtrlCompareProp,
    )

    device = FakeDevice()
    device.properties[(0, 13)] = b"\x00\x02\xa0\x62"  # 4 octets; the 5th is masked out
    application = _application(
        LdCtrlCompareProp(
            obj_idx=0,
            prop_id=13,
            start_element=1,
            count=1,
            inline_data=b"\x00\x00\xa0\x62\x14",
            mask=b"\x00\x00\xff\xff\x00",  # version octet not checked
        )
    )
    runner = LoadProcedureRunner(
        application, DownloadImage(segments=(), properties=()), DeviceProgrammer(device)
    )
    await runner.run()  # must not raise: nothing checkable lies beyond the reply


# Error the Zennio DIMinBOX ap1 procedure maps to success around an optional
# unload of interface object 5 (absent on a PeiType 0 variant): rejected write
# to a protected or non-existing resource.
_RESOURCE_WRITE_PROTECTED_ERROR = 3221498632


def _empty_image() -> DownloadImage:
    return DownloadImage(segments=(), properties=())


async def test_map_error_tolerates_unload_of_absent_object() -> None:
    # ap1 brackets the unload of object 5 with a MapError that maps the
    # absent-object error to success, then resets it. The unload must be
    # tolerated instead of aborting the download.
    application = _application(
        LdCtrlMapError(original_error=_RESOURCE_WRITE_PROTECTED_ERROR, mapped_error=0),
        LdCtrlUnload(lsm_idx=5, occurrence=0),
        LdCtrlMapError(
            original_error=_RESOURCE_WRITE_PROTECTED_ERROR,
            mapped_error=_RESOURCE_WRITE_PROTECTED_ERROR,
        ),
        LdCtrlUnload(lsm_idx=1, occurrence=0),
    )
    runner, device = _runner(application, _empty_image())
    device.absent_objects = {5}

    await runner.run()  # must not raise

    assert 5 not in device.load_states  # the rejected write did not take
    assert device.load_states[1] == LoadState.UNLOADED  # the present object unloaded


async def test_unload_of_absent_object_without_mapping_fails() -> None:
    # Without a MapError bracket, the same rejected unload is a real failure.
    application = _application(LdCtrlUnload(lsm_idx=5, occurrence=0))
    runner, device = _runner(application, _empty_image())
    device.absent_objects = {5}

    with pytest.raises(PropertyAccessRejected):
        await runner.run()


async def test_map_error_reset_rearms_the_failure() -> None:
    # A MapError that maps the error back to itself resets tolerance, so a later
    # unload of the absent object fails again.
    application = _application(
        LdCtrlMapError(original_error=_RESOURCE_WRITE_PROTECTED_ERROR, mapped_error=0),
        LdCtrlMapError(
            original_error=_RESOURCE_WRITE_PROTECTED_ERROR,
            mapped_error=_RESOURCE_WRITE_PROTECTED_ERROR,
        ),
        LdCtrlUnload(lsm_idx=5, occurrence=0),
    )
    runner, device = _runner(application, _empty_image())
    device.absent_objects = {5}

    with pytest.raises(PropertyAccessRejected):
        await runner.run()


async def test_map_error_does_not_hide_a_different_rejected_object() -> None:
    # The mapping tolerates the bracketed access only; a different object the
    # device also rejects after the reset still fails.
    application = _application(
        LdCtrlMapError(original_error=_RESOURCE_WRITE_PROTECTED_ERROR, mapped_error=0),
        LdCtrlUnload(lsm_idx=5, occurrence=0),
        LdCtrlMapError(
            original_error=_RESOURCE_WRITE_PROTECTED_ERROR,
            mapped_error=_RESOURCE_WRITE_PROTECTED_ERROR,
        ),
        LdCtrlUnload(lsm_idx=6, occurrence=0),
    )
    runner, device = _runner(application, _empty_image())
    device.absent_objects = {5, 6}

    with pytest.raises(PropertyAccessRejected):
        await runner.run()


async def test_map_error_for_a_different_error_does_not_tolerate() -> None:
    # A mapping armed for an unrelated error value must not swallow a rejection
    # that reports a different error, even while the arm window is open.
    application = _application(
        LdCtrlMapError(original_error=0xC0000001, mapped_error=0),
        LdCtrlUnload(lsm_idx=5, occurrence=0),
        LdCtrlMapError(original_error=0xC0000001, mapped_error=0xC0000001),
    )
    runner, device = _runner(application, _empty_image())
    device.absent_objects = {5}

    with pytest.raises(PropertyAccessRejected):
        await runner.run()


def _m112_application(*controls: object) -> Application:
    application = _application(*controls)
    application.program.mask_version = "MV-0701"
    return application


async def test_m112_unload_writes_memory_control() -> None:
    """MV-0701 drives LSM 1 through the 11-octet memory record at 0x0104."""
    application = _m112_application(LdCtrlUnload(lsm_idx=1, occurrence=0))
    runner, device = _runner(application, _empty_image())
    device.load_states[1] = LoadState.LOADED

    await runner.run()

    writes = [
        payload
        for payload in device.sent
        if isinstance(payload, MemoryWrite) and payload.address == 0x0104
    ]
    assert len(writes) == 1
    assert writes[0].data == bytes.fromhex("1400000000000000000000")
    assert device.load_states[1] == LoadState.UNLOADED
    assert not [
        payload
        for payload in device.sent
        if isinstance(payload, PropertyValueWrite)
        and payload.property_id == PID_LOAD_STATE_CONTROL
    ]
    assert any(
        isinstance(payload, MemoryRead) and payload.address == 0xB6EA
        for payload in device.sent
    )


async def test_m112_load_complete_uses_application_status() -> None:
    """LSM 3 status is the ApplicationLoadStatus byte at 0xB6EC."""
    application = _m112_application(
        LdCtrlLoad(lsm_idx=3),
        LdCtrlLoadCompleted(lsm_idx=3),
    )
    runner, device = _runner(application, _empty_image())

    await runner.run()

    records = [
        payload.data[0]
        for payload in device.sent
        if isinstance(payload, MemoryWrite) and payload.address == 0x0104
    ]
    assert records == [0x31, 0x32]
    assert device.load_states[3] == LoadState.LOADED
    assert any(
        isinstance(payload, MemoryRead) and payload.address == 0xB6EC
        for payload in device.sent
    )


async def test_m112_pei_task_segment_skips_missing_status() -> None:
    """LSM 5 has no LoadStatus on MV-0701; ETS skips the PEI AllocAbsTask."""
    application = _m112_application(
        LdCtrlTaskSegment(lsm_idx=5, address=0x4400, occurrence=0)
    )
    runner, device = _runner(application, _empty_image())

    await runner.run()

    assert not [
        payload
        for payload in device.sent
        if isinstance(payload, MemoryWrite) and payload.address == 0x0104
    ]
    assert not [
        payload
        for payload in device.sent
        if isinstance(payload, MemoryRead) and payload.address == 0xB6EE
    ]


async def test_m112_task_segment_keeps_manufacturer() -> None:
    """ETS writes the product manufacturer into BIM M112 AllocAbsTask records."""
    application = _m112_application(
        LdCtrlTaskSegment(lsm_idx=1, address=0x4000, occurrence=0)
    )
    runner, device = _runner(application, _empty_image())
    device.load_states[1] = LoadState.LOADING

    await runner.run()

    writes = [
        payload
        for payload in device.sent
        if isinstance(payload, MemoryWrite) and payload.address == 0x0104
    ]
    # M-0072, application 1, version 1. Not replaced with 0x0002.
    assert writes[0].data == bytes.fromhex("1302004000010072000101")


async def test_m112_task_segment_patches_manufacturer_when_requested() -> None:
    """LegacyPatchManufacturerIdInTaskSegment still forces manufacturer 0x0002."""
    application = _m112_application(
        LdCtrlTaskSegment(lsm_idx=1, address=0x4000, occurrence=0)
    )
    application.program.static = SimpleNamespace(  # type: ignore[attr-defined]
        options=SimpleNamespace(legacy_patch_manufacturer_id_in_task_segment=True)
    )
    runner, device = _runner(application, _empty_image())
    device.load_states[1] = LoadState.LOADING

    await runner.run()

    writes = [
        payload
        for payload in device.sent
        if isinstance(payload, MemoryWrite) and payload.address == 0x0104
    ]
    assert writes[0].data == bytes.fromhex("1302004000010002000101")


async def test_m112_abs_segment_sends_length() -> None:
    """ETS AbsSegment keeps the XML length, with a reserved octet after type."""
    application = _m112_application(
        LdCtrlAbsSegment(
            lsm_idx=1,
            occurrence=0,
            seg_type=0,
            address=0x4000,
            size=0x01FF,
            access=0xFF,
            mem_type=3,
            seg_flags=0x80,
        )
    )
    runner, device = _runner(application, _empty_image())

    await runner.run()

    writes = [
        payload
        for payload in device.sent
        if isinstance(payload, MemoryWrite) and payload.address == 0x0104
    ]
    assert writes[0].data == bytes.fromhex("130000400001ffff038000")


async def test_abs_segment_writes_initialized_bytes_in_partial_scope() -> None:
    """Allocating a segment must flash the code seed, even on a parameter download."""
    image = DownloadImage(
        segments=(
            MemorySegment(
                address=0x4B20,
                data=b"\xaa\xbb\xcc\xdd",
                mask=b"\x00\x00\x00\x00",
                initialized_mask=b"\xff\xff\xff\xff",
            ),
        ),
        properties=(),
    )
    application = _application(
        LdCtrlAbsSegment(
            lsm_idx=3,
            occurrence=0,
            seg_type=0,
            address=0x4B20,
            size=4,
            access=0xFF,
            mem_type=3,
            seg_flags=0x80,
        )
    )
    device = _device()
    runner = LoadProcedureRunner(
        application,
        image,
        DeviceProgrammer(device),
        scope=DownloadScope.PARAMETERS,
    )

    await runner.run()

    assert bytes(device.memory[0x4B20 + i] for i in range(4)) == b"\xaa\xbb\xcc\xdd"
