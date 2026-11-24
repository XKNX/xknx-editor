"""A linked group object always sends on one of its group addresses: its first link, when no
link of it is marked sending, so the association table follows the group object numbers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from xknx.profile.const import ResourceObjectType

from xknxeditor.download.image import GroupCommunication, build_image
from xknxeditor.download.project_data import (
    GroupObjectLink,
    group_communication_from_device,
    with_sending_links,
)
from xknxeditor.download.tables import Association, build_association_table
from xknxeditor.download.tables_systemb import build_association_table_b

from .test_module_objects_systemb import _APP, _INSTANCE_OBJECT, _application


def _link(ref: str, address: int, sending: bool) -> GroupObjectLink:
    return GroupObjectLink(
        com_object_ref_id=ref, group_address=address, sending=sending
    )


def test_the_first_link_of_an_object_without_a_sending_one_sends() -> None:
    links = [
        _link("A", 1, False),
        _link("A", 2, False),
        _link("B", 3, False),
        _link("B", 4, True),
    ]
    assert [link.sending for link in with_sending_links(links)] == [
        True,
        False,
        False,
        True,
    ]


def test_the_association_table_follows_the_object_numbers() -> None:
    # Issue #336: objects 2, 3 and 125 had their only link not sending and came last.
    numbers = [21, 22, 23, 61, 129, 133, 2, 3, 125]
    links = [
        _link(f"O-{number}", index, number not in (2, 3, 125))
        for index, number in enumerate(numbers, start=1)
    ]
    associations = [
        Association(link.group_address, int(link.com_object_ref_id[2:]), link.sending)
        for link in with_sending_links(links)
    ]
    ordered = sorted(numbers)
    table = build_association_table_b(associations)
    assert [table[2 + 2 * i + 1] for i in range(len(numbers))] == ordered
    table = build_association_table(associations)
    assert [table[1 + 2 * i + 1] for i in range(len(numbers))] == ordered


def test_the_project_links_of_a_device() -> None:
    def link(address: int, sending: bool) -> Any:
        return SimpleNamespace(
            group_address=SimpleNamespace(address=address), is_sending=sending
        )

    device: Any = SimpleNamespace(
        com_objects=[
            SimpleNamespace(ref_id="A", links=[link(1, False), link(2, False)]),
            SimpleNamespace(ref_id="B", links=[link(3, False), link(4, True)]),
        ]
    )
    assert [link.sending for link in group_communication_from_device(device)] == [
        True,
        False,
        False,
        True,
    ]


def test_an_image_of_links_without_a_sending_one() -> None:
    image = build_image(
        _application(),
        group_communication=GroupCommunication(
            device_address=0x1101,
            links=(
                _link(_INSTANCE_OBJECT, 0x0801, True),
                # Object 1 linked only by a link not marked sending.
                _link(f"{_APP}_O-1_R-1", 0x0802, False),
            ),
        ),
    )
    tables = {segment.object_type: segment.data for segment in image.relative_segments}
    association = tables[ResourceObjectType.OBJECT_ASSOCIATION_TABLE]
    # Object 1 (group address index 2) before object 301 (index 1), two octets per field.
    assert association == bytes.fromhex("0002") + bytes.fromhex("00020001" + "0001012d")
