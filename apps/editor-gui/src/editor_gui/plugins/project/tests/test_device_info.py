"""Device info: value formatting, the result sections and the queue item (no bus)."""

from __future__ import annotations

from concurrent.futures import Future
from typing import Any

from editor_gui.device_info import (
    DeviceInfo,
    GroupObjectInfo,
    firmware_text,
    flags_text,
    group_objects,
    hex_value,
    is_system_b,
    same_application,
    serial_text,
    size_text,
)
from editor_gui.plugins.project.device_info_view import Toned, device_info_sections
from editor_gui.plugins.project.program_queue import (
    OperationState,
    ProgramQueue,
    QueueItem,
)
from xknxeditor.download.load_state import LoadState
from xknxeditor.recover import AppId, RawGroupCommunication
from xknxeditor.recover.tables_decode import DecodedGroupObject, DecodedLink


def _object(number: int, **flags: bool) -> DecodedGroupObject:
    return DecodedGroupObject(
        number=number,
        priority="Low",
        communication=flags.get("c", False),
        read=flags.get("r", False),
        write=flags.get("w", False),
        transmit=flags.get("t", False),
        update=flags.get("u", False),
        read_on_init=flags.get("i", False),
        size_code=0,
        object_size="1 Bit",
    )


def test_value_formats() -> None:
    assert hex_value(b"Open") == "$4F70656E"
    assert hex_value(b"") is None
    assert serial_text(bytes.fromhex("00FAA7581922")) == "00FA:A7581922"
    assert firmware_text(0x000D) == "[0] 0.13"
    assert firmware_text(0x0841) == "[1] 1.1"
    assert size_text("1 Bit") == "1 bit"
    assert size_text("2 Bytes") == "2 Byte"
    assert size_text("1 Byte") == "1 Byte"


def test_flags_in_read_write_communication_transmit_update_init_order() -> None:
    assert flags_text(_object(1, r=True, c=True, t=True)) == "R-CT--"
    assert flags_text(_object(2, w=True, c=True, u=True, i=True)) == "-WC-UI"


def test_same_application() -> None:
    app_id = AppId("M-00FA", 0xA012, 0x51)
    assert same_application("M-00FA_A-A012-51-9712", app_id)
    assert not same_application("M-00FA_A-A012-52-9712", app_id)
    assert not same_application("M-0083_A-A012-51-9712", app_id)


def test_group_objects_list_linked_objects_with_their_addresses() -> None:
    tables = RawGroupCommunication(
        device_address=None,
        group_addresses=[0x0B02, 0x0C00],
        links=[
            DecodedLink(0x0C00, 290, False),
            DecodedLink(0x0B02, 20, True),
            DecodedLink(0x1820, 290, False),
        ],
        group_objects={20: _object(20, r=True, c=True, t=True)},
    )
    assert group_objects(tables) == [
        GroupObjectInfo(20, "1 bit", "R-CT--", (0x0B02,)),
        GroupObjectInfo(290, None, None, (0x0C00, 0x1820)),
    ]


def test_sections_leave_out_unread_values() -> None:
    info = DeviceInfo(
        individual_address="1.0.27",
        mask_version=0x07B0,
        manufacturer_id=0x00FA,
        serial_number=bytes.fromhex("00FAA7581922"),
        firmware_version=0x000D,
        programming_mode=False,
        application=AppId("M-00FA", 0xA012, 0x51),
        application_load_state=LoadState.LOADED,
        address_table_load_state=LoadState.LOADED,
        group_objects=[GroupObjectInfo(20, "1 bit", "R-CT--", (0x0B02,))],
    )
    sections = device_info_sections(
        info,
        manufacturers={"M-00FA": "KNX Association"},
        application_name="LED-Dimmer",
        group_address=lambda value: f"{value >> 11}/{(value >> 8) & 7}/{value & 0xFF}",
    )
    assert [title for title, _ in sections] == [
        "General",
        "Application Program",
        "Group Communication",
    ]
    general = dict(sections[0][1])
    assert general["Mask Version"] == "07B0"
    assert general["Device Manufacturer"] == "KNX Association"
    assert general["Serial Number"] == "00FA:A7581922"
    assert general["Firmware Version"] == "[0] 0.13"
    assert general["Programming Mode"] == "Off"
    assert "Order Number" not in general
    assert sections[1][1] == [
        ("Application Program", "KNX Association LED-Dimmer"),
        ("Application Version", "5.1"),
        ("Load State", "Loaded"),
    ]
    assert sections[2][1] == [
        ("Address Table Load State", "Loaded"),
        ("Obj#20 (1 bit, R-CT--)", "1/3/2"),
    ]


def test_device_info_items_are_not_merged_with_downloads() -> None:
    started: list[QueueItem] = []
    futures: list[Future[Any]] = []
    finished: list[tuple[QueueItem, Any]] = []

    def start(item: QueueItem) -> Future[Any]:
        started.append(item)
        futures.append(Future())
        return futures[-1]

    queue = ProgramQueue(
        is_busy=lambda: bool(futures) and not futures[-1].done(),
        start=start,
        on_finished=lambda item, result: finished.append((item, result)),
    )
    queue.enqueue(
        QueueItem(node_id=1, address="1.0.27", name="D", scope=None, device_info=False)
    )
    queue.enqueue(QueueItem(node_id=2, address="1.0.28", name="E", scope=None))
    queue.enqueue(
        QueueItem(node_id=2, address="1.0.28", name="E", scope=None, device_info=True)
    )
    assert len(queue.active) == 3
    futures[0].set_result("info")
    assert finished == [(started[0], "info")]
    assert started[0].state is OperationState.FINISHED


def test_system_b_masks() -> None:
    assert is_system_b(0x07B0)
    assert is_system_b(0x57B0)
    assert not is_system_b(0x0705)
    assert not is_system_b(None)


def test_sections_compare_with_the_project() -> None:
    info = DeviceInfo(
        individual_address="1.0.27",
        programming_mode=True,
        error_code=0,
        application=AppId("M-00FA", 0xA012, 0x51),
        group_objects=[
            GroupObjectInfo(1, "1 bit", "", (0x0B02,)),
            GroupObjectInfo(2, "1 bit", "", (0x0B03,)),
        ],
    )
    sections = device_info_sections(
        info,
        manufacturers={},
        application_name=None,
        group_address=str,
        project_version=0x50,
        expected={1: {0x0B02}, 2: {0x0B04}},
    )
    general = dict(sections[0][1])
    assert isinstance(general["Programming Mode"], Toned)
    assert general["Programming Mode"].tone == "differs"
    assert general["Status"] == "no fault reported"
    assert general["Status"].tone == "ok"
    version = dict(sections[1][1])["Application Version"]
    assert version.startswith("5.1") and "5.0" in version
    assert version.tone == "differs"
    objects = dict(sections[2][1])
    assert objects["Obj#1 (1 bit)"].tone == "ok"
    assert objects["Obj#2 (1 bit)"].tone == "differs"


def test_unknown_application_is_reported_in_group_communication() -> None:
    info = DeviceInfo(individual_address="1.1.5", application_unknown=True)
    sections = device_info_sections(
        info, manufacturers={}, application_name=None, group_address=str
    )
    assert sections[-1][0] == "Group Communication"
    assert "not in the catalog" in sections[-1][1][-1][1]
