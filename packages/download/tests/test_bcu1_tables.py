"""BCU1 group tables share the application EEPROM.

Mask MV-0011 (AssociationTable_Bcu1, GroupObjectTable_Bcu11) keeps the
one-octet association list and a 3-octet group-object record inside the same
segment as the application id. A System B record laid out from the segment
start overwrites that id. A second link to one object is a second entry;
unlinked objects are omitted.
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
    # One link: object 1, address-table index 1. Objects 0 and 2 are omitted.
    assert association.data == bytes.fromhex("010101")

    address_table = next(segment for segment in segments if segment.address == 0x0116)
    assert address_table.data == bytes.fromhex("021133090d")


def test_bcu1_association_keeps_every_link() -> None:
    """ETS writes one entry per link. A receiving address is not dropped."""
    nodes = [
        _com_object(0, read=False, write=True, transmit=False, update=True),
        _com_object(1, read=True, write=True, transmit=True, update=True),
    ]
    segments = _group_communication_segments(
        _application("MV-0011"),  # type: ignore[arg-type]
        _UI(nodes),  # type: ignore[arg-type]
        {_SEGMENT: _BASE},
        {_SEGMENT: (_seed(), b"\x00" * 256)},
        GroupCommunication(
            device_address=0x114A,
            links=(
                GroupObjectLink(
                    com_object_ref_id="O-0_R", group_address=0x110D, sending=True
                ),
                GroupObjectLink(
                    com_object_ref_id="O-0_R", group_address=0x1000, sending=False
                ),
                GroupObjectLink(
                    com_object_ref_id="O-1_R", group_address=0x110E, sending=True
                ),
                GroupObjectLink(
                    com_object_ref_id="O-1_R", group_address=0x1001, sending=False
                ),
            ),
        ),
    )
    association = next(segment for segment in segments if segment.address == 0x013B)
    # Sorted addresses 0x1000, 0x1001, 0x110D, 0x110E are indexes 1..4.
    # Sending links first, then the receiving links.
    assert association.data == bytes.fromhex("040300040101000201")


def test_system_b_overlay_still_starts_at_the_segment() -> None:
    """The BCU1 layout is mask 001x only. MV-0701 still uses the 4-octet record."""
    segments = _segments("MV-0701")
    eeprom = next(segment for segment in segments if len(segment.data) == 256)
    assert eeprom.data[5] == 0x93
    assert eeprom.initialized_mask is not None
    assert eeprom.initialized_mask[0x0D] == 0xFF
    association = next(segment for segment in segments if segment.address == 0x013B)
    assert association.data == bytes.fromhex("010101")


def test_m112_clears_communication_without_a_dynamic_ref() -> None:
    """A static group object with no ComObjectRef must not keep the seed's communication bit."""
    application = _application("MV-0701")
    application.program.static.com_object_table.com_object.append(
        SimpleNamespace(id="O-3", number=3)
    )
    nodes = [
        _com_object(0, read=False, write=True, transmit=False, update=True),
        _com_object(1, read=True, write=True, transmit=True, update=True),
        _com_object(2, read=True, write=False, transmit=False, update=True),
    ]
    seed = bytearray(_seed())
    # Five records: object 3 is static-only, object 4 is a hole with no ComObject.
    seed[0] = 5
    seed[5 + 3 * 4] = 0xCF
    seed[5 + 4 * 4] = 0xDF
    segments = _group_communication_segments(
        application,  # type: ignore[arg-type]
        _UI(nodes),  # type: ignore[arg-type]
        {_SEGMENT: _BASE},
        {_SEGMENT: (bytes(seed), b"\x00" * len(seed))},
        GroupCommunication(
            device_address=0x1133,
            links=(
                GroupObjectLink(
                    com_object_ref_id="O-1_R", group_address=0x090D, sending=True
                ),
            ),
        ),
    )
    eeprom = next(segment for segment in segments if len(segment.data) == 256)
    assert eeprom.data[5 + 3 * 4] == 0xCB
    assert eeprom.data[5 + 4 * 4] == 0xDB


def test_bcu2_keeps_communication_on_static_slots() -> None:
    """GroupObjectTable_Bcu2 is not the M112 record; its other slots stay as seeded."""
    application = _application("MV-0020")
    application.program.static.com_object_table.com_object.append(
        SimpleNamespace(id="O-3", number=3)
    )
    nodes = [_com_object(0, read=False, write=True, transmit=False, update=True)]
    seed = bytearray(_seed())
    seed[0] = 5
    seed[5 + 3 * 4] = 0xCF
    seed[5 + 4 * 4] = 0xDF
    segments = _group_communication_segments(
        application,  # type: ignore[arg-type]
        _UI(nodes),  # type: ignore[arg-type]
        {_SEGMENT: _BASE},
        {_SEGMENT: (bytes(seed), b"\x00" * len(seed))},
        GroupCommunication(device_address=0x1133, links=()),
    )
    eeprom = next(segment for segment in segments if len(segment.data) == 256)
    assert eeprom.mask is not None
    assert eeprom.mask[5 + 3 * 4] == 0x00
    assert eeprom.mask[5 + 4 * 4] == 0x00
    # A full download writes initialized bytes. BCU2 has none, so the seed
    # stays out of that write.
    assert eeprom.initialized_mask is None
