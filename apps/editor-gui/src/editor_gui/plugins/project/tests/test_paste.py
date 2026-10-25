"""Paste Special: copies of devices, building parts, trades, functions, group addresses, groups,
lines and segments, with the group address options, one undo step each."""

from __future__ import annotations

from pathlib import Path

import pytest

from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.paste import PasteError, PasteTarget
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.proj.core.paste import Allocation, GroupAddressCopy, PasteOptions

_APP_ID = "M-0008_A-7072-21-5CC3-O000A"


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        cand = (
            parent
            / "packages"
            / "prod"
            / "tests"
            / "fixtures"
            / "gira_2gang_button_interface.knxprod"
        )
        if cand.exists():
            return cand
    raise FileNotFoundError("gira_2gang_button_interface.knxprod not found")


@pytest.fixture
def setup(tmp_path: Path) -> tuple[ProjectService, CatalogService]:
    cat = CatalogService(tmp_path / "c.xknxcatalog")
    cat.import_knxprod(_fixture())
    proj = ProjectService(cat)
    proj.set_logger(Logger(LogService(), "project"))
    proj.new(tmp_path / "p.xknx")
    return proj, cat


def _add(
    proj: ProjectService, cat: CatalogService, name: str, space_id: int | None = None
) -> int:
    product = next(p for p in cat.get_products() if p.application_id == _APP_ID)
    app = cat.get_application(_APP_ID)
    assert app is not None
    dev_id = proj.add_device(
        product.product_ref_id,
        product.hardware2program_ref_id,
        name,
        app,
        space_id=space_id,
    )
    assert dev_id is not None
    return dev_id


def _linked(proj: ProjectService, node_id: int) -> dict[str, set[int]]:
    """The group addresses each group object of the device links, by object id."""
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    return {
        co.id: {
            link.group_address_id for link in proj.get_links_for_com_object(co.db_id)
        }
        for co in device.get_visible_com_objects()
        if co.db_id is not None
    }


def _first_object(proj: ProjectService, node_id: int) -> tuple[str, int]:
    device = proj.find_device_by_node_id(node_id)
    assert device is not None
    co = next(co for co in device.get_visible_com_objects() if co.db_id is not None)
    assert co.db_id is not None
    return co.id, co.db_id


def _addresses(proj: ProjectService) -> dict[str, str]:
    """Every group address as shown ("1/1/1") with its name."""
    return {ga.address: ga.name for ga in proj.group_addresses}


def _last_history(proj: ProjectService) -> str:
    return proj.history()[0].display_text


def test_device_copies_keep_name_comment_and_links(
    setup: tuple[ProjectService, CatalogService],
) -> None:
    proj, cat = setup
    source = _add(proj, cat, "Kitchen switch")
    proj.set_comment("device", source, "{\\rtf1 Behind the door}")
    co_id, co_db = _first_object(proj, source)
    ga = proj.create_group_address_value(0x0901, "Light")
    assert ga is not None
    proj.link_com_object_to_ga(co_db, ga, is_sending=True)
    segment = proj.device_segments()[source]
    before = len(proj.history())

    created = proj.paste(
        "device",
        [source],
        PasteTarget("segment", segment),
        PasteOptions(count=2, group_addresses=GroupAddressCopy.KEEP),
    )

    assert len(created) == 2
    addresses = set()
    for node_id in created:
        copy = proj.find_device_by_node_id(node_id)
        assert copy is not None
        assert copy.name == "Kitchen switch"
        assert proj.comment("device", node_id) == "{\\rtf1 Behind the door}"
        assert _linked(proj, node_id)[co_id] == {ga}
        addresses.add(copy.individual_address)
    source_device = proj.find_device_by_node_id(source)
    assert source_device is not None
    assert len(addresses | {source_device.individual_address}) == 3
    # One undo step, named after the copy.
    assert len(proj.history()) == before + 1
    assert _last_history(proj).startswith("Copied 'Kitchen switch' to ")
    proj.undo()
    assert {d.node_id for d in proj.devices} == {source}


def test_dont_copy_links_nothing(setup: tuple[ProjectService, CatalogService]) -> None:
    proj, cat = setup
    source = _add(proj, cat, "Switch")
    co_id, co_db = _first_object(proj, source)
    ga = proj.create_group_address_value(0x0901, "Light")
    assert ga is not None
    proj.link_com_object_to_ga(co_db, ga, is_sending=True)
    segment = proj.device_segments()[source]

    (copy,) = proj.paste(
        "device", [source], PasteTarget("segment", segment), PasteOptions()
    )

    assert _linked(proj, copy)[co_id] == set()


def test_create_new_gives_each_copy_its_own_group_addresses(
    setup: tuple[ProjectService, CatalogService],
) -> None:
    proj, cat = setup
    source = _add(proj, cat, "Switch")
    co_id, co_db = _first_object(proj, source)
    ga = proj.create_group_address_value(0x0901, "Light")  # 1/1/1
    assert ga is not None
    proj.link_com_object_to_ga(co_db, ga, is_sending=True)
    segment = proj.device_segments()[source]
    options = PasteOptions(
        count=2,
        group_addresses=GroupAddressCopy.CREATE_NEW,
        target_range="2/3",
    )

    created = proj.paste("device", [source], PasteTarget("segment", segment), options)

    # The middle group 2/3 was made first; each copy links a new "Light" there.
    assert _addresses(proj) == {"1/1/1": "Light", "2/3/0": "Light", "2/3/1": "Light"}
    new = [next(iter(_linked(proj, node_id)[co_id])) for node_id in created]
    assert len(set(new)) == 2
    assert ga not in new
    assert _linked(proj, source)[co_id] == {ga}


def test_create_new_needs_a_group(setup: tuple[ProjectService, CatalogService]) -> None:
    proj, cat = setup
    source = _add(proj, cat, "Switch")
    segment = proj.device_segments()[source]
    options = PasteOptions(group_addresses=GroupAddressCopy.CREATE_NEW, target_range="")

    with pytest.raises(PasteError) as raised:
        proj.paste("device", [source], PasteTarget("segment", segment), options)

    assert raised.value.reason == "target_range"
    assert {d.node_id for d in proj.devices} == {source}


@pytest.mark.parametrize(
    ("options", "address"),
    [
        (PasteOptions(), "1/1/0"),
        (PasteOptions(allocation=Allocation.APPEND), "1/1/6"),
        (PasteOptions(allocation=Allocation.START_WITH, start_with=10), "1/1/10"),
        (PasteOptions(allocation=Allocation.OFFSET, offset=3), "1/1/4"),
    ],
)
def test_group_address_copies_go_where_the_allocation_says(
    setup: tuple[ProjectService, CatalogService], options: PasteOptions, address: str
) -> None:
    proj, _cat = setup
    source = proj.create_group_address_value(0x0901, "Light")  # 1/1/1
    assert source is not None
    proj.create_group_address_value(0x0905, "Blind")  # 1/1/5
    proj.set_group_address_dpt(source, "DPST-1-1")
    middle = proj.get_group_address(source)
    assert middle is not None
    tree = proj.get_group_range_tree()
    group = tree[0].children[0]

    (copy,) = proj.paste(
        "group_address", [source], PasteTarget("group_range", group.id), options
    )

    info = proj.get_group_address(copy)
    assert info is not None
    assert info.address == address
    assert info.name == "Light"
    assert info.datapoint_type == "DPST-1-1"


def test_middle_and_main_group_copies(
    setup: tuple[ProjectService, CatalogService],
) -> None:
    proj, _cat = setup
    proj.create_group_address_value(0x0901, "Light")  # 1/1/1
    proj.create_group_address_value(0x1000, "Other")  # 2/0/0
    tree = proj.get_group_range_tree()
    main_1, main_2 = tree
    middle = main_1.children[0]

    proj.paste(
        "group_range",
        [middle.id],
        PasteTarget("group_range", main_2.id),
        PasteOptions(),
    )
    assert _addresses(proj)["2/1/1"] == "Light"

    proj.paste(
        "group_range", [main_1.id], PasteTarget("group_range", None), PasteOptions()
    )
    # The lowest free main group, its middle groups and addresses at the same places.
    assert _addresses(proj)["0/1/1"] == "Light"
    assert _last_history(proj).startswith("Copied ")


def test_group_copies_refused_without_room(
    setup: tuple[ProjectService, CatalogService],
) -> None:
    proj, _cat = setup
    proj.create_group_address_value(0x0901, "Light")
    main = proj.get_group_range_tree()[0]
    middle = main.children[0]

    with pytest.raises(PasteError) as raised:
        proj.paste(
            "group_range",
            [middle.id],
            PasteTarget("group_range", main.id),
            PasteOptions(count=8),
        )

    assert raised.value.reason == "group_ranges"


def test_building_part_copies_with_functions_and_devices(
    setup: tuple[ProjectService, CatalogService],
) -> None:
    proj, cat = setup
    building = proj.create_space(None, "Building", "House")
    assert building is not None
    floor = proj.create_space(building, "Floor", "Ground floor")
    assert floor is not None
    room = proj.create_space(floor, "Room", "Kitchen")
    assert room is not None
    device = _add(proj, cat, "Switch", space_id=room)
    ga = proj.create_group_address_value(0x0901, "Light")
    assert ga is not None
    function = proj.create_function(room, "FT-1", "Ceiling light")
    assert function is not None
    proj.add_function_group_address(function, ga, "SwitchOnOff")

    (copy,) = proj.paste(
        "space",
        [room],
        PasteTarget("space", floor),
        PasteOptions(group_addresses=GroupAddressCopy.KEEP),
    )

    house = next(s for s in proj.get_space_tree() if s.id == building)
    new = next(r for r in house.children[0].children if r.id == copy)
    assert new.name == "Kitchen"
    assert [d.name for d in new.devices] == ["Switch"]
    assert new.devices[0].id != device
    (fn,) = new.functions
    assert fn.name == "Ceiling light"
    assert [(r.group_address_id, r.role) for r in fn.group_addresses] == [
        (ga, "SwitchOnOff")
    ]
    assert _last_history(proj) == "Copied 'Kitchen' to 'Ground floor'."


def test_trade_copies_hold_device_copies_without_address(
    setup: tuple[ProjectService, CatalogService],
) -> None:
    proj, cat = setup
    trade = proj.create_trade(None, "Lighting")
    assert trade is not None
    device = _add(proj, cat, "Switch")
    proj.add_trade_device(trade, device)

    (copy,) = proj.paste("trade", [trade], PasteTarget("trade", None), PasteOptions())

    new = next(t for t in proj.get_trade_tree() if t.id == copy)
    assert new.name == "Lighting"
    (link,) = new.devices
    assert link.device.id != device
    assert link.device.individual_address is None


def test_segment_and_line_copies(setup: tuple[ProjectService, CatalogService]) -> None:
    proj, cat = setup
    device = _add(proj, cat, "Switch")
    segment = proj.device_segments()[device]
    line = next(
        line
        for area in proj.get_areas()
        for line in proj.get_lines(area.id)
        if line.main_segment_id == segment
    )
    area = next(a for a in proj.get_areas() if a.id == line.area_id)
    before = {d.node_id for d in proj.devices}

    (line_copy,) = proj.paste(
        "line", [line.id], PasteTarget("area", area.id), PasteOptions()
    )

    copied = {d.node_id for d in proj.devices} - before
    assert len(copied) == 1
    copy = proj.find_device_by_node_id(next(iter(copied)))
    assert copy is not None
    assert copy.name == "Switch"
    new_line = next(ln for ln in proj.get_lines(area.id) if ln.id == line_copy)
    assert copy.individual_address == (f"{area.area_number}.{new_line.line_number}.1")

    (segment_copy,) = proj.paste(
        "segment", [segment], PasteTarget("line", line_copy), PasteOptions()
    )
    (sub,) = next(ln for ln in proj.get_lines(area.id) if ln.id == line_copy).segments
    assert sub.id == segment_copy
    # The device copy keeps no number taken in the line: the next free one.
    numbers = {
        d.individual_address
        for d in proj.devices
        if proj.device_segments()[d.node_id] == segment_copy
    }
    assert numbers == {f"{area.area_number}.{new_line.line_number}.2"}
