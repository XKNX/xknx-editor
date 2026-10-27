"""BCU1 group tables share the application EEPROM.

Mask MV-0011 (AssociationTable_Bcu1, GroupObjectTable_Bcu11) keeps the
one-octet association list and a 3-octet group-object record inside the same
segment as the application id. A System B record laid out from the segment
start overwrites that id. A second link to one object is a second entry;
unlinked objects are omitted.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest
from xknx.profile.const import ResourceObjectType

from xknxeditor.download.errors import ImageError
from xknxeditor.download.factory_image import (
    FactoryImage,
    FactoryLocation,
    FactorySegment,
)
from xknxeditor.download.image import (
    GroupCommunication,
    MemorySegment,
    _communication_by_link,
    _group_communication_relative_segments,
    _group_communication_segments,
)
from xknxeditor.download.project_data import GroupObjectLink
from xknxeditor.namespaces.intermediate.com_object_priority_t import ComObjectPriority
from xknxeditor.namespaces.intermediate.enable_t import Enable
from xknxeditor.prod.parser_v2.ui.com_object import UiComObject

_SEGMENT = "AS-0100"
_BASE = 0x0100


def _com_object(
    number: int,
    *,
    read: bool,
    write: bool,
    transmit: bool,
    update: bool,
    read_on_init: bool = False,
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
        read_on_init=read_on_init,
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


def _static_object(
    number: int,
    *,
    communication: bool = False,
    read: bool = False,
    write: bool = False,
    priority: ComObjectPriority = ComObjectPriority.LOW,
) -> SimpleNamespace:
    """A group object of the application's static table, with its own flags."""

    def flag(enabled: bool) -> Enable:
        return Enable.ENABLED if enabled else Enable.DISABLED

    return SimpleNamespace(
        id=f"O-{number}",
        number=number,
        priority=priority,
        communication_flag=flag(communication),
        read_flag=flag(read),
        write_flag=flag(write),
        transmit_flag=Enable.DISABLED,
        update_flag=Enable.DISABLED,
        read_on_init_flag=Enable.DISABLED,
    )


def _application(
    mask: str,
    *,
    dynamic: bool = False,
    association_offset: int = 59,
    application_id: str = "M-00FA_A-0001-01-0000",
) -> SimpleNamespace:
    objects = [_static_object(number) for number in range(3)]
    refs = [
        SimpleNamespace(id=f"O-{number}_R", ref_id=f"O-{number}") for number in range(3)
    ]

    def table(offset: int, max_entries: int = 17) -> SimpleNamespace:
        return SimpleNamespace(
            code_segment=_SEGMENT, offset=offset, max_entries=max_entries
        )

    return SimpleNamespace(
        program=SimpleNamespace(
            id=application_id,
            mask_version=mask,
            dynamic_table_management=dynamic,
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
                address_table=table(22, 0 if dynamic else 17),
                association_table=table(association_offset),
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


def test_m112_record_starts_at_the_table() -> None:
    """The BCU1 layout is mask 001x only. MV-0701 uses the 4-octet record, from the
    table's offset in the segment; the application id before it stays."""
    segments = _segments("MV-0701")
    eeprom = next(segment for segment in segments if len(segment.data) == 256)
    assert eeprom.data[4:13] == bytes.fromhex("01540b01fe13000000")
    assert eeprom.data[_m112_flags(0)] == 0x93
    assert eeprom.initialized_mask is not None
    assert eeprom.initialized_mask[0x0D] == 0xFF
    association = next(segment for segment in segments if segment.address == 0x013B)
    assert association.data == bytes.fromhex("010101")


# The group object table of the fixtures lies at offset 94 of the segment.
_TABLE = 94


def _m112_flags(number: int) -> int:
    """Offset of an object's flag byte in a GroupObjectTable_M112 at ``_TABLE``."""
    return _TABLE + 5 + number * 4


def _bcu2_flags(number: int) -> int:
    """Offset of an object's flag byte in a GroupObjectTable_Bcu2 at ``_TABLE``."""
    return _TABLE + 2 + number * 3 + 1


def _m112_segments(
    application: SimpleNamespace, seed: bytearray, nodes: list[UiComObject]
) -> MemorySegment:
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
    return next(segment for segment in segments if len(segment.data) == 256)


_NODES = [
    _com_object(0, read=False, write=True, transmit=False, update=True),
    _com_object(1, read=True, write=True, transmit=True, update=True),
    _com_object(2, read=True, write=False, transmit=False, update=True),
]


def test_m112_clears_communication_without_a_dynamic_ref() -> None:
    """A static group object with no ComObjectRef and a hole in the numbering must not
    keep the seed's communication bit. A record past the highest object number stays."""
    application = _application("MV-0701")
    objects = application.program.static.com_object_table.com_object
    objects.extend((_static_object(3), _static_object(5)))
    seed = bytearray(_seed())
    # Object 3 and 5 are static-only, object 4 is a hole with no ComObject, record 6 is
    # past the table.
    seed[_TABLE] = 6
    for number, flags in ((3, 0xCF), (4, 0xDF), (5, 0xCF), (6, 0xDF)):
        seed[_m112_flags(number)] = flags
    eeprom = _m112_segments(application, seed, _NODES)
    assert [eeprom.data[_m112_flags(number)] for number in range(3, 7)] == [
        0xCB,
        0xDB,
        0xCB,
        0xDF,
    ]
    assert eeprom.mask is not None
    assert eeprom.mask[_m112_flags(6)] == 0


def test_m112_count_is_the_number_of_records() -> None:
    """The count is the highest object number + 1. A full download writes the product's
    octet, which differs here; a partial download writes the count. Both write the
    whole table: the count, the RAM flags pointer and every record."""
    application = _application("MV-0705")
    application.program.static.com_object_table.com_object.append(_static_object(4))
    seed = bytearray(_seed())
    seed[_TABLE] = 0xFE
    eeprom = _m112_segments(application, seed, _NODES)
    assert eeprom.partial_octets == ((_TABLE, 5),)
    assert eeprom.mask is not None
    end = _TABLE + 3 + 5 * 4
    assert eeprom.mask[_TABLE:end] == b"\xff" * (end - _TABLE)
    assert not any(eeprom.mask[:_TABLE]) and not any(eeprom.mask[end:])

    full = dict(eeprom.masked_runs(include_initialized=True))
    assert full[_BASE][_TABLE] == 0xFE
    (address, data), *_ = eeprom.masked_runs(partial=True)
    assert address == _BASE + _TABLE
    assert data[0] == 5
    assert data[1:3] == seed[_TABLE + 1 : _TABLE + 3]


def test_m112_count_that_matches_the_product_is_kept_everywhere() -> None:
    application = _application("MV-0701")
    seed = bytearray(_seed())
    seed[_TABLE] = 3
    eeprom = _m112_segments(application, seed, _NODES)
    assert eeprom.partial_octets == ()
    assert eeprom.masked_runs(partial=True)[0][1][0] == 3


def test_bcu2_writes_3_octet_records_at_the_table() -> None:
    """GroupObjectTable_Bcu2: count, RAM flags pointer, then value pointer, flags and type
    per object number. An active object gets its configured flags and size, a static one
    its own flags without communication, a hole none; bit 5 of the flags and the top two
    bits of the type stay as the seed has them."""
    application = _application("MV-0020")
    objects = application.program.static.com_object_table.com_object
    objects[2] = _static_object(2, communication=True, read=True, write=True)
    objects.append(_static_object(4, priority=ComObjectPriority.HIGH))
    seed = bytearray(_seed())
    seed[_TABLE : _TABLE + 2 + 6 * 3] = bytes.fromhex(
        "09ce" + "00ffc0" + "01ff00" + "02df00" + "03ff00" + "04ff00" + "05ff00"
    )
    eeprom = _m112_segments(application, seed, _NODES[:2])
    record = _TABLE + 2
    assert eeprom.data[record : record + 5 * 3] == bytes.fromhex(
        # Object 0: update, write, low priority, bit 5 from the seed; 1 bit (0) under
        # the seed's top bits.
        "00b3c0"
        # Object 1: linked, every flag but read on init; bit 5 from the seed.
        "01ff00"
        # Object 2: static, read and write, communication cleared (unused).
        "021b00"
        # Hole: no flags, low priority; bit 5 from the seed.
        "032300"
        # Object 4: static, no flags, high priority.
        "042100"
    )
    assert eeprom.data[record + 5 * 3 : record + 6 * 3] == bytes.fromhex("05ff00")
    assert eeprom.mask is not None
    assert eeprom.mask[_TABLE : record + 5 * 3] == b"\xff" * (2 + 5 * 3)
    assert not any(eeprom.mask[record + 5 * 3 :])
    assert eeprom.partial_octets == ((_TABLE, 5),)
    # A full download writes the table only, not the rest of the seed.
    assert eeprom.initialized_mask is None


def test_bcu1_sets_bit_7_of_every_flag_byte() -> None:
    """BCU1 records are the BCU2 ones; bit 7 of each flag byte is set, also for an
    object without the update flag, a static object and a hole."""
    application = _application("MV-0012")
    application.program.static.com_object_table.com_object.append(
        _static_object(4, read=True)
    )
    seed = bytearray(_seed())
    seed[_TABLE : _TABLE + 2 + 5 * 3] = bytes.fromhex(
        "09ce" + "000700" + "010700" + "020700" + "030700" + "040700"
    )
    nodes = [_com_object(0, read=True, write=True, transmit=True, update=False)]
    eeprom = _m112_segments(application, seed, nodes)
    flags = [eeprom.data[_bcu2_flags(number)] for number in range(5)]
    # Object 0 configured; 1 and 2 static, not instantiated; 3 a hole; 4 static.
    assert flags == [0xDB, 0x83, 0x83, 0x83, 0x8B]
    assert eeprom.data[_TABLE + 2 + 2] == 0x00
    assert eeprom.partial_octets == ((_TABLE, 5),)
    assert eeprom.mask is not None
    assert eeprom.mask[_TABLE : _TABLE + 2 + 5 * 3] == b"\xff" * 17


def test_bcu2_hidden_pointers_are_not_written() -> None:
    application = _application("MV-0025", application_id="M-00FD_A-0001-11-1234")
    eeprom = _m112_segments(application, bytearray(_seed()), _NODES)
    assert eeprom.mask is not None
    assert [eeprom.mask[_TABLE + 2 + number * 3] for number in range(3)] == [0, 0, 0]
    assert eeprom.mask[_bcu2_flags(0)] == 0xFF


@pytest.mark.parametrize(
    ("mask", "flag_offset"),
    [
        # BCU1: 3-octet records after the count, the flag in the third octet.
        ("MV-0011", lambda number: 94 + 1 + number * 3 + 2),
        # BCU2: 3-octet records after the count and the RAM flags pointer.
        ("MV-0020", _bcu2_flags),
        # M112 (System 7): 4-octet records after a 5-octet header, the flag first.
        ("MV-0705", _m112_flags),
    ],
)
def test_memory_mapped_tables_keep_the_segment_selector(
    mask: str, flag_offset: Callable[[int], int]
) -> None:
    """Bit 5 of a memory-mapped record selects the value's memory segment. It stays as
    the product has it: a read-on-init object does not set it, and an object without
    read-on-init does not clear it."""
    nodes = [
        _com_object(
            0, read=False, write=True, transmit=True, update=True, read_on_init=True
        ),
        _com_object(1, read=True, write=True, transmit=True, update=True),
    ]
    seed = bytearray(_seed())
    seed[flag_offset(0)] = 0x9F
    seed[flag_offset(1)] = 0xBF
    segments = _group_communication_segments(
        _application(mask),  # type: ignore[arg-type]
        _UI(nodes),  # type: ignore[arg-type]
        {_SEGMENT: _BASE},
        {_SEGMENT: (bytes(seed), b"\x00" * len(seed))},
        GroupCommunication(
            device_address=0x1133,
            links=tuple(
                GroupObjectLink(
                    com_object_ref_id=f"O-{number}_R",
                    group_address=0x0900 + number,
                    sending=True,
                )
                for number in range(2)
            ),
        ),
    )
    eeprom = next(segment for segment in segments if len(segment.data) == 256)
    # Update, transmit, write, communication, low priority; bit 5 from the seed.
    assert eeprom.data[flag_offset(0)] == 0xD7
    assert eeprom.data[flag_offset(1)] == 0xFF


def test_bcu2_factory_table_keeps_each_objects_communication_flag() -> None:
    """With the table in the factory image, a static object keeps its own communication
    flag."""
    application = _application("MV-0021")
    objects = application.program.static.com_object_table.com_object
    objects[2] = _static_object(2, communication=True)
    segments = _group_communication_segments(
        application,  # type: ignore[arg-type]
        _UI(_NODES[:2]),  # type: ignore[arg-type]
        {_SEGMENT: _BASE},
        {_SEGMENT: (_seed(), b"\x00" * 256)},
        GroupCommunication(device_address=0x1133, links=()),
        _factory(FactoryLocation.MEMORY, 0, 0, _BASE + _TABLE),
    )
    eeprom = next(segment for segment in segments if len(segment.data) == 256)
    # Communication, low priority; bit 5 of the seed's 8f is clear.
    assert eeprom.data[_bcu2_flags(2)] == 0x07


def _dynamic_segments(
    links: int, *, association_offset: int = 59
) -> list[MemorySegment]:
    seed = _seed()
    return _group_communication_segments(
        _application("MV-0012", dynamic=True, association_offset=association_offset),  # type: ignore[arg-type]
        _UI(
            [
                _com_object(n, read=True, write=True, transmit=True, update=True)
                for n in range(3)
            ]
        ),  # type: ignore[arg-type]
        {_SEGMENT: _BASE},
        {_SEGMENT: (seed, b"\x00" * len(seed))},
        GroupCommunication(
            device_address=0x1133,
            links=tuple(
                GroupObjectLink(
                    com_object_ref_id=f"O-{i % 3}_R",
                    group_address=0x0900 + i,
                    sending=i < 3,
                )
                for i in range(links)
            ),
        ),
    )


def _association_pointer(segments: list[MemorySegment]) -> int:
    eeprom = next(segment for segment in segments if len(segment.data) == 256)
    assert eeprom.mask is not None
    assert eeprom.mask[0x11] == 0xFF
    return eeprom.data[0x11]


def test_dynamic_tables_keep_the_declared_association_place() -> None:
    """The address table ends before the declared association table: it stays there."""
    segments = _dynamic_segments(2)
    assert any(segment.address == 0x013B for segment in segments)
    assert _association_pointer(segments) == 0x3B


def test_dynamic_tables_follow_a_grown_address_table() -> None:
    """With no room declared for addresses, the association table follows them."""
    segments = _dynamic_segments(4, association_offset=25)
    # Count, own address and 4 group addresses: 0116h..0120h.
    association = next(segment for segment in segments if segment.address == 0x0121)
    assert association.data[0] == 4
    assert _association_pointer(segments) == 0x21


def test_dynamic_tables_end_at_the_group_object_table() -> None:
    """Both tables share 0116h..015Dh; 18 links need 76 octets there."""
    _dynamic_segments(17)
    with pytest.raises(ImageError, match="are free before the group object table"):
        _dynamic_segments(18)


def _factory(
    location: FactoryLocation, ref: int, pid: int, address: int
) -> FactoryImage:
    return FactoryImage(
        (FactorySegment(location, ref, pid, address, 4, bytes(4), None),)
    )


def _m112_table(factory_image: FactoryImage | None) -> bytes:
    application = _application("MV-0701")
    objects = application.program.static.com_object_table.com_object
    objects.extend((_static_object(3), _static_object(5)))
    for com_object in objects:
        com_object.communication_flag = Enable.ENABLED
    seed = bytearray(_seed())
    # Object 3 and 5 are static-only, object 4 is a hole with no ComObject.
    seed[_TABLE] = 6
    for number, flags in ((3, 0xCF), (4, 0xDF), (5, 0xCF)):
        seed[_m112_flags(number)] = flags
    segments = _group_communication_segments(
        application,  # type: ignore[arg-type]
        _UI(_NODES),  # type: ignore[arg-type]
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
        factory_image,
    )
    return next(segment for segment in segments if len(segment.data) == 256).data


def test_m112_factory_table_keeps_each_objects_communication_flag() -> None:
    """With the group object table in the factory image, every object keeps its own
    communication flag, linked or not; a hole without a group object has none."""
    by_link = _m112_table(None)
    # The table sits at the com object table's address (segment base + offset 94).
    own = _m112_table(_factory(FactoryLocation.MEMORY, 0, 0, _BASE + _TABLE))
    for number in (0, 2):
        assert not by_link[_m112_flags(number)] & 0x04
        assert own[_m112_flags(number)] == by_link[_m112_flags(number)] | 0x04
    assert own[_m112_flags(1)] == by_link[_m112_flags(1)]
    assert own[_m112_flags(3)] == 0xCF
    assert own[_m112_flags(4)] == 0xDB
    assert own[_m112_flags(5)] == 0xCF


def test_m112_factory_image_elsewhere_keeps_the_link_rule() -> None:
    """A factory image without the table leaves the communication bit to the links."""
    assert _m112_table(_factory(FactoryLocation.MEMORY, 0, 0, _BASE)) == _m112_table(
        None
    )


def _system_b_table(factory_image: FactoryImage | None) -> bytes:
    application = _application("MV-07B0")
    objects = [
        SimpleNamespace(id=f"O-{number}", number=number, communication_flag=flag)
        for number, flag in (
            (1, Enable.ENABLED),
            (2, Enable.ENABLED),
            (3, Enable.ENABLED),
            (4, Enable.DISABLED),
        )
    ]
    application.program.static.com_object_table.com_object = objects
    nodes = [
        _com_object(1, read=True, write=True, transmit=True, update=True),
        _com_object(2, read=True, write=False, transmit=False, update=True),
    ]
    segments = _group_communication_relative_segments(
        application,  # type: ignore[arg-type]
        _UI(nodes),  # type: ignore[arg-type]
        GroupCommunication(
            device_address=0x1133,
            links=(
                GroupObjectLink(
                    com_object_ref_id="O-1_R", group_address=0x090D, sending=True
                ),
            ),
        ),
        factory_image,
    )
    return next(
        segment.data
        for segment in segments
        if segment.object_type == ResourceObjectType.OBJECT_GROUP_OBJECT_TABLE
    )


def test_system_b_factory_table_keeps_each_objects_communication_flag() -> None:
    """MV-07B0 holds the table in object 3's relative memory: when the factory image has it,
    unlinked and not instantiated objects keep their communication flag."""
    by_link = _system_b_table(None)
    own = _system_b_table(_factory(FactoryLocation.RELATIVE_MEMORY, 3, 7, 0))
    # Count, then [flags][size] for objects 1..4.
    assert by_link[:2] == own[:2] == b"\x00\x04"
    assert own[2:4] == by_link[2:4]
    assert not by_link[4] & 0x04
    assert own[4:6] == bytes([by_link[4] | 0x04, by_link[5]])
    assert by_link[6:10] == bytes(4)
    # Object 3 is not instantiated: only its communication flag; object 4 has none.
    assert own[6:10] == b"\x04\x00\x00\x00"


@pytest.mark.parametrize(
    ("factory_image", "by_link"),
    [
        (None, True),
        # Another object's relative memory is not the table.
        (_factory(FactoryLocation.RELATIVE_MEMORY, 4, 7, 0), True),
        (_factory(FactoryLocation.RELATIVE_MEMORY, 3, 7, 0), False),
        # Properties never hold the table.
        (_factory(FactoryLocation.APP_PROPERTY, 3, 7, 0), True),
    ],
)
def test_communication_follows_links_unless_the_factory_image_holds_the_table(
    factory_image: FactoryImage | None, by_link: bool
) -> None:
    application = _application("MV-07B0")
    assert (
        _communication_by_link(
            application,  # type: ignore[arg-type]
            factory_image,
            None,
        )
        is by_link
    )
