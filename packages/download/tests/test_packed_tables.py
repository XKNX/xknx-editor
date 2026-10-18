"""Property-mapped M112 masks pack the association table against the address table.

ETS (Merten 4806, mask MV-0705, device 1.1.56) allocates the address table at its
used length and starts the association table on the next octet, extending that
segment to the declared end. MV-0701 keeps the product-data addresses.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from xknx.telegram.apci import MemoryWrite, PropertyValueWrite

from xknxeditor.download.image import (
    DownloadImage,
    GroupCommunication,
    MemorySegment,
    _group_communication_segments,
)
from xknxeditor.download.load_state import PID_LOAD_STATE_CONTROL
from xknxeditor.download.procedure import LoadProcedureRunner
from xknxeditor.download.programmer import DeviceProgrammer
from xknxeditor.download.project_data import GroupObjectLink
from xknxeditor.namespaces.intermediate.ld_ctrl_abs_segment_t import LdCtrlAbsSegment
from xknxeditor.namespaces.intermediate.ld_ctrl_load_t import LdCtrlLoad
from xknxeditor.namespaces.intermediate.ld_ctrl_task_segment_t import LdCtrlTaskSegment
from xknxeditor.prod.parser_v2.ui.com_object import UiComObject

from .conftest import FakeDevice

if True:
    from xknxeditor.prod import Application

_ADDR = "AS-4000"
_ASSOC = "AS-41FF"


def _com_object(number: int) -> UiComObject:
    return UiComObject(
        ref_id=f"O-{number}_R",
        name=str(number),
        function_text="",
        number=number,
        dpt_codes=(),
        object_size="1 Bit",
        priority="Low",
        communication=True,
        read=True,
        write=True,
        transmit=True,
        update=True,
        read_on_init=False,
        read_locked=False,
        write_locked=False,
        transmit_locked=False,
        update_locked=False,
        read_on_init_locked=False,
    )


class _UI:
    def __init__(self, nodes: list[UiComObject]) -> None:
        self._nodes = nodes

    def ui(self) -> list[UiComObject]:
        return self._nodes

    def instantiated_com_object_ref_ids(self) -> set[str]:
        return {node.ref_id for node in self._nodes}


def _application(mask: str, *, association_address: int = 0x41FF) -> SimpleNamespace:
    association_id = "AS-41FF" if association_address == 0x41FF else "AS-4200"
    return SimpleNamespace(
        manufacturer_id="M-000C",
        load_procedures=None,
        program=SimpleNamespace(
            mask_version=mask,
            pei_type=0,
            application_number=0x4806,
            application_version=0x11,
            module_defs=None,
            is_secure_enabled=False,
            static=SimpleNamespace(
                script=None,
                options=None,
                code=SimpleNamespace(
                    absolute_segment=[
                        SimpleNamespace(id=_ADDR, address=0x4000, size=511),
                        SimpleNamespace(
                            id=association_id,
                            address=association_address,
                            size=511,
                        ),
                    ],
                    relative_segment=[],
                ),
                parameter_types=None,
                parameters=None,
                parameter_refs=None,
                messages=None,
                com_object_table=None,
                com_object_refs=None,
                parameter_calculations=None,
                parameter_validations=None,
                allocators=None,
                address_table=SimpleNamespace(
                    code_segment=_ADDR, offset=0, max_entries=254
                ),
                association_table=SimpleNamespace(
                    code_segment=association_id, offset=0, max_entries=255
                ),
            ),
        ),
    )


def _segments(mask: str, *, association_address: int = 0x41FF) -> list[MemorySegment]:
    nodes = [_com_object(1)]
    association_id = "AS-41FF" if association_address == 0x41FF else "AS-4200"
    return _group_communication_segments(
        _application(mask, association_address=association_address),  # type: ignore[arg-type]
        _UI(nodes),  # type: ignore[arg-type]
        {_ADDR: 0x4000, association_id: association_address},
        {},
        GroupCommunication(
            device_address=0x1138,
            links=(
                GroupObjectLink(
                    com_object_ref_id="O-1_R", group_address=0x0A02, sending=True
                ),
            ),
        ),
    )


def test_mv0705_places_the_association_table_after_the_address_table() -> None:
    segments = _segments("MV-0705")
    address = next(segment for segment in segments if segment.address == 0x4000)
    assert address.data == bytes.fromhex("0211380a02")
    association = next(segment for segment in segments if segment.address == 0x4005)
    assert association.data == bytes.fromhex("010101")
    assert all(segment.address != 0x41FF for segment in segments)


def test_mv0701_keeps_the_declared_association_address() -> None:
    segments = _segments("MV-0701")
    association = next(segment for segment in segments if segment.address == 0x41FF)
    assert association.data == bytes.fromhex("010101")


def test_a_gap_between_the_tables_is_not_packed() -> None:
    segments = _segments("MV-0705", association_address=0x4200)
    assert any(segment.address == 0x4200 for segment in segments)
    assert all(segment.address != 0x4005 for segment in segments)


def test_bcu2_association_table_is_not_packed() -> None:
    """AssociationTable_Bcu2 (MV-0020/0021/0025) is property-mapped too, but it is
    a different table reached through its own pointer, not the M112 list ETS packs.
    Even with the two segments declared contiguous, the association table stays at
    its declared address."""
    segments = _segments("MV-0020")
    association = next(segment for segment in segments if segment.address == 0x41FF)
    assert association.data == bytes.fromhex("010101")
    assert all(segment.address != 0x4005 for segment in segments)


def _abs(lsm: int, address: int, size: int) -> LdCtrlAbsSegment:
    return LdCtrlAbsSegment(
        lsm_idx=lsm,
        occurrence=0,
        seg_type=0,
        address=address,
        size=size,
        access=0xF2,
        mem_type=3,
        seg_flags=0x80,
    )


def _image(*, association_address: int) -> DownloadImage:
    return DownloadImage(
        segments=(
            MemorySegment(0x4000, bytes(25), mask=b"\xff" * 25),
            MemorySegment(association_address, b"\x0d\x03", mask=b"\xff\xff"),
        ),
        properties=(),
    )


async def test_mv0705_allocates_the_packed_tables_as_properties() -> None:
    """The load events match the ETS group-monitor record for 1.1.56."""
    application = cast(
        "Application",
        _application("MV-0705"),
    )
    image = _image(association_address=0x4019)
    device = FakeDevice()
    runner = LoadProcedureRunner(
        application,
        image,
        DeviceProgrammer(device),
        controls=[
            _abs(1, 0x4000, 511),
            _abs(2, 0x41FF, 511),
            LdCtrlTaskSegment(lsm_idx=2, address=0x41FF, occurrence=0),
        ],
    )

    await runner.run()

    assert not [
        payload
        for payload in device.sent
        if isinstance(payload, MemoryWrite) and payload.address == 0x0104
    ]
    events = [
        payload.data
        for payload in device.sent
        if isinstance(payload, PropertyValueWrite)
        and payload.property_id == PID_LOAD_STATE_CONTROL
    ]
    assert events == [
        bytes.fromhex("030040000019f2038000"),
        bytes.fromhex("0300401903e5f2038000"),
        bytes.fromhex("0302401900000c480611"),
    ]
    assert device.memory[0x4019] == 0x0D
    assert device.memory[0x401A] == 0x03
    assert 0x41FF not in device.memory


async def test_mv0705_skips_the_pei_load_ets_does_not_send() -> None:
    """The procedure's LSM 5 task segment and load are absent from the ETS record.

    Object 5 is the first application object on MV-0705. It has no load-state
    property, so the write comes back with zero elements.
    """
    application = cast("Application", _application("MV-0705"))
    device = FakeDevice()
    runner = LoadProcedureRunner(
        application,
        _image(association_address=0x4019),
        DeviceProgrammer(device),
        controls=[
            LdCtrlTaskSegment(lsm_idx=5, address=0x43FE, occurrence=0),
            LdCtrlLoad(lsm_idx=5),
        ],
    )

    await runner.run()

    assert not [
        payload
        for payload in device.sent
        if isinstance(payload, PropertyValueWrite)
        and payload.property_id == PID_LOAD_STATE_CONTROL
    ]


async def test_mv0701_still_allocates_the_declared_association_segment() -> None:
    application = cast("Application", _application("MV-0701"))
    image = _image(association_address=0x41FF)
    device = FakeDevice()
    runner = LoadProcedureRunner(
        application,
        image,
        DeviceProgrammer(device),
        controls=[_abs(2, 0x41FF, 511)],
    )

    await runner.run()

    records = [
        payload.data
        for payload in device.sent
        if isinstance(payload, MemoryWrite) and payload.address == 0x0104
    ]
    assert records == [bytes.fromhex("23000041ff01fff2038000")]
    assert device.memory[0x41FF] == 0x0D
    assert 0x4019 not in device.memory
