"""BCU1 group tables share the application EEPROM.

The Siemens N 561 (mask MV-0011) keeps one association slot per object and a
3-octet group-object record inside the same segment as the application id.
A System B record laid out from the segment start overwrites that id.
"""

from __future__ import annotations

from types import SimpleNamespace

from xknxeditor.download.image import (
    GroupCommunication,
    _group_communication_segments,
)
from xknxeditor.download.project_data import GroupObjectLink
from xknxeditor.prod.parser_v2.ui.com_object import UiComObject

_SEGMENT = "AS-0100"
_BASE = 0x0100


def _com_object(
    number: int, *, read: bool, write: bool, transmit: bool, update: bool
) -> UiComObject:
    return UiComObject(
        ref_id=f"O-{number}_R",
        name=str(number),
        function_text="",
        number=number,
        dpt_codes=(),
        object_size="1 Bit",
        priority="Low",
        communication=True,
        read=read,
        write=write,
        transmit=transmit,
        update=update,
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


def _application(mask: str) -> SimpleNamespace:
    objects = [SimpleNamespace(id=f"O-{number}", number=number) for number in range(3)]
    refs = [
        SimpleNamespace(id=f"O-{number}_R", ref_id=f"O-{number}") for number in range(3)
    ]

    def table(offset: int) -> SimpleNamespace:
        return SimpleNamespace(code_segment=_SEGMENT, offset=offset, max_entries=17)

    return SimpleNamespace(
        program=SimpleNamespace(
            mask_version=mask,
            module_defs=None,
            static=SimpleNamespace(
                script=None,
                code=SimpleNamespace(
                    absolute_segment=[SimpleNamespace(id=_SEGMENT, address=_BASE)],
                    relative_segment=[],
                ),
                parameter_types=None,
                parameters=None,
                parameter_refs=None,
                messages=None,
                com_object_table=SimpleNamespace(
                    com_object=objects, code_segment=_SEGMENT, offset=94
                ),
                com_object_refs=SimpleNamespace(com_object_ref=refs),
                parameter_calculations=None,
                parameter_validations=None,
                allocators=None,
                address_table=table(22),
                association_table=table(59),
            ),
        )
    )


def _seed() -> bytes:
    seed = bytearray(b"\xa5" * 256)
    # Application id and table pointers, as the manufacturer image carries them.
    seed[4:13] = bytes.fromhex("01540b01fe13000000")
    seed[0x0D] = 0xFF
    seed[0x0E:0x16] = bytes.fromhex("e063e83b5ea0c388")
    seed[94:104] = bytes.fromhex("09ced89700d49700d98f")
    return bytes(seed)


def _segments(mask: str) -> list:
    nodes = [
        _com_object(0, read=False, write=True, transmit=False, update=True),
        _com_object(1, read=True, write=True, transmit=True, update=True),
        _com_object(2, read=True, write=False, transmit=False, update=True),
    ]
    seed = _seed()
    return _group_communication_segments(
        _application(mask),  # type: ignore[arg-type]
        _UI(nodes),  # type: ignore[arg-type]
        {_SEGMENT: _BASE},
        {_SEGMENT: (seed, b"\x00" * len(seed))},
        GroupCommunication(
            device_address=0x1133,
            links=(
                GroupObjectLink(
                    com_object_ref_id="O-1_R", group_address=0x090D, sending=True
                ),
            ),
        ),
    )


def test_bcu1_tables_keep_the_application_header() -> None:
    """MV-0011 writes slots and flag bytes, and leaves the run byte to the procedure."""
    segments = _segments("MV-0011")
    eeprom = next(segment for segment in segments if len(segment.data) == 256)
    assert eeprom.data[4:13] == bytes.fromhex("01540b01fe13000000")
    assert eeprom.data[0x0E:0x16] == bytes.fromhex("e063e83b5ea0c388")
    assert eeprom.data[94:104] == bytes.fromhex("09ced89300d4df00d98b")
    assert eeprom.initialized_mask is not None
    assert eeprom.initialized_mask[0x0D] == 0
    assert all(
        address != 0x010D
        for address, _data in eeprom.masked_runs(include_initialized=True)
    )

    association = next(segment for segment in segments if segment.address == 0x013B)
    # One slot per object: unlinked objects are 0xFE, object 1 uses address index 1.
    assert association.data == bytes.fromhex("03fe000101fe02")

    address_table = next(segment for segment in segments if segment.address == 0x0116)
    assert address_table.data == bytes.fromhex("021133090d")


def test_system_b_overlay_still_starts_at_the_segment() -> None:
    """The BCU1 layout is mask 001x only. MV-0701 still uses the 4-octet record."""
    segments = _segments("MV-0701")
    eeprom = next(segment for segment in segments if len(segment.data) == 256)
    assert eeprom.data[5] == 0x93
    assert eeprom.initialized_mask is not None
    assert eeprom.initialized_mask[0x0D] == 0xFF
    association = next(segment for segment in segments if segment.address == 0x013B)
    assert association.data == bytes.fromhex("010101")
