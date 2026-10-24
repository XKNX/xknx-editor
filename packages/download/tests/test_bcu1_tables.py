"""BCU1 group tables share the application EEPROM.

Mask MV-0011 (AssociationTable_Bcu1, GroupObjectTable_Bcu11) keeps the
one-octet association list and a 3-octet group-object record inside the same
segment as the application id. A System B record laid out from the segment
start overwrites that id. A second link to one object is a second entry;
unlinked objects are omitted.
"""

from __future__ import annotations

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
from xknxeditor.namespaces.intermediate.enable_t import Enable
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


def _application(
    mask: str, *, dynamic: bool = False, association_offset: int = 59
) -> SimpleNamespace:
    objects = [SimpleNamespace(id=f"O-{number}", number=number) for number in range(3)]
    refs = [
        SimpleNamespace(id=f"O-{number}_R", ref_id=f"O-{number}") for number in range(3)
    ]

    def table(offset: int, max_entries: int = 17) -> SimpleNamespace:
        return SimpleNamespace(
            code_segment=_SEGMENT, offset=offset, max_entries=max_entries
        )

    return SimpleNamespace(
        program=SimpleNamespace(
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


@pytest.mark.parametrize(
    ("application_id", "pointers"),
    [("M-00FD_A-0002-10-0000", True), ("M-00FD_A-0006-10-0000", False)],
)
def test_bcu2_writes_the_bcu1_record_layout(
    application_id: str, pointers: bool
) -> None:
    """GroupObjectTable_Bcu2 has the BCU 1 layout (3/5/1 4.18.4): a count, the RAM flags table
    pointer, then [value pointer][config][type] per object, at the table's offset."""
    from xknxeditor.download.tables import com_object_flag_byte, size_code

    application = _application("MV-0020")
    application.program.id = application_id
    nodes = [_com_object(0, read=False, write=True, transmit=False, update=True)]
    seed = _seed()
    segments = _group_communication_segments(
        application,  # type: ignore[arg-type]
        _UI(nodes),  # type: ignore[arg-type]
        {_SEGMENT: _BASE},
        {_SEGMENT: (seed, b"\x00" * len(seed))},
        GroupCommunication(device_address=0x1133, links=()),
    )
    eeprom = next(segment for segment in segments if len(segment.data) == 256)
    assert eeprom.mask is not None
    table = 94
    # The count stays the device's, the RAM flags table pointer is written as seeded.
    assert eeprom.mask[table] == 0x00
    assert (eeprom.mask[table + 1], eeprom.data[table + 1]) == (0xFF, seed[table + 1])
    flags = com_object_flag_byte(
        priority="Low",
        communication=False,
        read=False,
        write=True,
        transmit=False,
        update=True,
        read_on_init=False,
    )
    record = table + 2
    assert eeprom.data[record + 1] == (seed[record + 1] & 0x20) | (flags & 0xDF)
    assert eeprom.data[record + 2] == (seed[record + 2] & 0xC0) | size_code("1 Bit")
    # Objects the configuration does not use keep their seeded record, without communication.
    second = record + 3
    assert eeprom.data[second + 1] == seed[second + 1] & ~0x04
    assert eeprom.data[second + 2] == seed[second + 2]
    for n in range(3):
        at = record + 3 * n
        assert (eeprom.mask[at + 1], eeprom.mask[at + 2]) == (0xFF, 0xFF)
        # The value pointers are written with the table, except for the listed applications.
        assert eeprom.mask[at] == (0xFF if pointers else 0x00)
        assert eeprom.data[at] == seed[at]
    # Nothing past the defined objects and nothing at the segment start (the old record).
    assert not any(eeprom.mask[:table])
    assert not any(eeprom.mask[record + 9 :])
    assert eeprom.initialized_mask is None


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
    objects.append(SimpleNamespace(id="O-3", number=3))
    for com_object in objects:
        com_object.communication_flag = Enable.ENABLED
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
        factory_image,
    )
    return next(segment for segment in segments if len(segment.data) == 256).data


def test_m112_factory_table_keeps_each_objects_communication_flag() -> None:
    """With the group object table in the factory image, every object keeps its own
    communication flag, linked or not; a hole without a group object has none."""
    by_link = _m112_table(None)
    # The table sits at the com object table's address (segment base + offset 94).
    own = _m112_table(_factory(FactoryLocation.MEMORY, 0, 0, _BASE + 94))
    for number in (0, 2):
        assert not by_link[5 + number * 4] & 0x04
        assert own[5 + number * 4] == by_link[5 + number * 4] | 0x04
    assert own[5 + 1 * 4] == by_link[5 + 1 * 4]
    assert own[5 + 3 * 4] == 0xCF
    assert own[5 + 4 * 4] == 0xDB


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
