"""The open project as Home Assistant's KNX integration reads it: the same as xknxproject gives
for the project file it was imported from, and what the editor changed since."""

from __future__ import annotations

import dataclasses
import json
import os
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from xknxproject import XKNXProj

from editor_gui.languages import product_language
from editor_gui.plugins.base import Logger
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.home_assistant.application_digest import (
    ApplicationDigest,
    ArgumentDef,
    BlockDef,
    ChannelDef,
    ObjectDef,
    digest_program,
)
from editor_gui.plugins.home_assistant.project_model import (
    CREATED_BY,
    ProjectModelUnavailable,
    _Builder,  # pyright: ignore[reportPrivateUsage]
    _DeviceContext,  # pyright: ignore[reportPrivateUsage]
    build_project_model,
    prepare_project_model,
)
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.namespaces.intermediate.allocator_t import Allocator
from xknxeditor.namespaces.intermediate.application_program_channel_t import (
    ApplicationProgramChannel,
    ComObjectParameterBlock,
)
from xknxeditor.namespaces.intermediate.com_object_ref_t import ComObjectRef
from xknxeditor.namespaces.intermediate.module_t_numeric_arg import ModuleNumericArg

TOOL = "XKNX Editor 1.2.3"


def _fixture() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = (
            parent
            / "packages/proj/tests/fixtures/xknx_test_project_no_password.knxproj"
        )
        if candidate.exists():
            return candidate
    pytest.skip("knxproj fixture not available")


def _import(
    tmp_path: Path, source: Path, password: str | None = None
) -> ProjectService:
    project = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    project.import_knxproj(source, tmp_path / "p.xknx", password=password)
    return project


def _normalized(project: Any) -> Any:
    """A project dictionary without what differs by design between the editor and a project
    file, as JSON values:

    - ``info``: the program (``created_by``, ``tool_version``) is the editor;
    - the group addresses of a group range in their address order: a project keeps no order
      of them within a range;
    - an area's or line's empty description as none: a project does not tell an empty
      description from none.
    """
    data = json.loads(json.dumps(project))
    for key in ("created_by", "tool_version"):
        data["info"].pop(key)

    def ranges(group_ranges: dict[str, Any]) -> None:
        for group_range in group_ranges.values():
            group_range["group_addresses"].sort(
                key=lambda a: [int(p) for p in a.replace("...", "/").split("/")]
            )
            ranges(group_range["group_ranges"])

    ranges(data["group_ranges"])
    for area in data["topology"].values():
        area["description"] = area["description"] or None
        for line in area["lines"].values():
            line["description"] = line["description"] or None
    return data


def _differences(ours: Any, theirs: Any, path: str = "") -> list[str]:
    if isinstance(ours, dict) and isinstance(theirs, dict):
        found: list[str] = []
        for key in sorted({*ours, *theirs}, key=str):
            if key not in ours or key not in theirs:
                found.append(
                    f"{path}/{key}: only in {'ours' if key in ours else 'theirs'}"
                )
            else:
                found += _differences(ours[key], theirs[key], f"{path}/{key}")
        return found
    return [] if ours == theirs else [f"{path}: {ours!r} != {theirs!r}"]


def test_the_model_equals_what_xknxproject_reads_from_the_project_file(
    tmp_path: Path,
) -> None:
    source = _fixture()
    project = _import(tmp_path, source)
    language = product_language()

    model = build_project_model(project, tool_version=TOOL, language_code=language)

    expected = XKNXProj(source, language=language).parse()
    assert _differences(_normalized(model), _normalized(expected)) == []
    assert model["info"]["created_by"] == CREATED_BY
    assert model["info"]["tool_version"] == TOOL
    assert (
        model["info"]["xknxproject_version"] == expected["info"]["xknxproject_version"]
    )


# Channels and folders, nested: CH-0 and the blocks are the program's, CH-7 to CH-9 are not;
# O-10_R-485 and O-11_R-486 have no link, O-4_R-1417 is directly under the device.
_TREE = (
    '<GroupObjectTree GroupObjectInstances="O-4_R-1417"><Nodes>'
    '<Node Type="Channel" RefId="CH-0" GroupObjectInstances="O-40_R-1433 O-10_R-485"><Nodes>'
    '<Node Type="Folder" RefId="PB-147" GroupObjectInstances="O-41_R-1434"><Nodes>'
    '<Node Type="Folder" RefId="PB-131" Text="Inner {{0:Default}}" '
    'GroupObjectInstances="O-70_R-1505" />'
    "</Nodes></Node>"
    '<Node Type="Channel" RefId="CH-9" Text="Sub" GroupObjectInstances="O-71_R-1506" />'
    '<Node Type="Folder" RefId="PB-213" GroupObjectInstances="O-11_R-486" />'
    "</Nodes></Node>"
    '<Node Type="Folder" RefId="PB-593" Text="Top" GroupObjectInstances="O-100_R-1577">'
    '<Nodes><Node Type="Channel" RefId="CH-8" GroupObjectInstances="O-101_R-1578" />'
    "</Nodes></Node>"
    '<Node Type="Channel" RefId="CH-7" GroupObjectInstances="O-10_R-485" />'
    "</Nodes></GroupObjectTree>"
)
# Nested trades, equally named siblings, each device in two trades.
_TRADES = (
    "<Trades>"
    '<Trade Id="P-01D2-0_T-1" Name="Licht" Number="1" Puid="50">'
    '<Trade Id="P-01D2-0_T-2" Name="Innen" Description="Wohnräume" Puid="51">'
    '<DeviceInstanceRef RefId="P-01D2-0_DI-1" />'
    "</Trade>"
    '<DeviceInstanceRef RefId="P-01D2-0_DI-2" />'
    '<DeviceInstanceRef RefId="P-01D2-0_DI-1" />'
    "</Trade>"
    '<Trade Id="P-01D2-0_T-3" Name="Licht" Comment="Kommentar" Puid="52">'
    '<DeviceInstanceRef RefId="P-01D2-0_DI-2" />'
    "</Trade>"
    "</Trades>"
)


def _with_trades_and_tree(tmp_path: Path) -> Path:
    """The fixture with :data:`_TREE` as the device's group object tree, a second device on
    its line and :data:`_TRADES`."""
    source = tmp_path / "trades.knxproj"
    with (
        zipfile.ZipFile(_fixture()) as original,
        zipfile.ZipFile(source, "w") as copy,
    ):
        for info in original.infolist():
            data = original.read(info)
            if info.filename.endswith("/0.xml"):
                text = data.decode("utf-8")
                text, trees = re.subn(r"<GroupObjectTree [^>]*/>", _TREE, text)
                device = text[
                    text.index("<DeviceInstance ") : text.index("</DeviceInstance>")
                ]
                second = (
                    device.split(">", 1)[0]
                    .replace('Id="P-01D2-0_DI-1"', 'Id="P-01D2-0_DI-2"')
                    .replace('Address="1"', 'Address="2"')
                    .replace('Puid="7"', 'Puid="90"')
                )
                text = text.replace(
                    "</DeviceInstance>", f"</DeviceInstance>{second} />", 1
                )
                text = text.replace("</GroupAddresses>", f"</GroupAddresses>{_TRADES}")
                assert trees == 1 and _TRADES in text
                data = text.encode("utf-8")
            copy.writestr(info, data)
    return source


def test_trades_and_group_object_trees_equal_what_xknxproject_reads(
    tmp_path: Path,
) -> None:
    source = _with_trades_and_tree(tmp_path)
    project = _import(tmp_path, source)
    language = product_language()

    model = build_project_model(project, tool_version=TOOL, language_code=language)

    expected = XKNXProj(source, language=language).parse()
    assert _differences(_normalized(model), _normalized(expected)) == []
    # What the comparison covers.
    devices = model["devices"]
    assert (devices["1.1.1"]["trade_id"], devices["1.1.2"]["trade_id"]) == (
        "P-01D2-0_T-2",
        "P-01D2-0_T-1",
    )
    assert [t["name"] for t in model["trades"]] == ["Licht", "Licht"]
    assert model["trades"][0]["devices"] == ["1.1.1", "1.1.2"]
    tree = devices["1.1.1"]["group_object_tree"]
    assert [(n["type"], n["identifier"]) for n in tree] == [
        ("Channel", "CH-0"),
        ("Folder", "PB-593"),
    ]
    inner = tree[0]["nodes"][0]["nodes"][0]
    assert (inner["name"], inner["communication_object_ids"]) == (
        "Inner Default",
        ["1.1.1/O-70_R-1505"],
    )
    assert list(devices["1.1.1"]["channels"]) == ["CH-0", "CH-9", "CH-8"]
    assert devices["1.1.1"]["channels"]["CH-0"]["communication_object_ids"] == [
        "1.1.1/O-40_R-1433",
        "1.1.1/O-41_R-1434",
        "1.1.1/O-70_R-1505",
    ]
    objects = model["communication_objects"]
    assert objects["1.1.1/O-71_R-1506"]["channel"] == "CH-9"
    assert objects["1.1.1/O-100_R-1577"]["channel"] is None


def test_trades_edited_in_the_editor_show_in_the_model(tmp_path: Path) -> None:
    project = _import(tmp_path, _with_trades_and_tree(tmp_path))
    device = next(d for d in project.devices if d.individual_address == "1.1.2")
    top = project.create_trade(None, "Heizung")
    assert top is not None
    sub = project.create_trade(top, "Bad")
    assert sub is not None
    project.set_trade_field(sub, "number", "7")
    project.add_trade_device(sub, device.node_id)
    renamed = next(t for t in project.get_trade_tree() if t.name == "Licht")
    project.set_trade_field(renamed.id, "name", "Beleuchtung")

    model = build_project_model(project, tool_version=TOOL)

    trades = model["trades"]
    assert [t["name"] for t in trades] == ["Beleuchtung", "Licht", "Heizung"]
    assert trades[2] == {
        "identifier": f"T-x{top}",
        "name": "Heizung",
        "number": "",
        "description": "",
        "project_uid": None,
        "devices": [],
        "trades": [
            {
                "identifier": f"T-x{sub}",
                "name": "Bad",
                "number": "7",
                "description": "",
                "project_uid": None,
                "devices": ["1.1.2"],
                "trades": [],
            }
        ],
    }
    # The editor moved the device out of the trades it was in.
    assert trades[0]["devices"] == ["1.1.1"]
    assert trades[1]["devices"] == []
    assert model["devices"]["1.1.2"]["trade_id"] == f"T-x{sub}"
    assert model["devices"]["1.1.1"]["trade_id"] == "P-01D2-0_T-2"


_OPT_IN = os.environ.get("XKNX_TEST_HA_KNXPROJ")


@pytest.mark.skipif(not _OPT_IN, reason="set XKNX_TEST_HA_KNXPROJ to a .knxproj")
def test_the_model_of_a_real_project_equals_what_xknxproject_reads(
    tmp_path: Path,
) -> None:
    """Also the product names differ by design here: the editor keeps those of the language
    the products were imported in."""
    assert _OPT_IN
    source = Path(_OPT_IN)
    password = os.environ.get("XKNX_TEST_HA_KNXPROJ_PASSWORD") or None
    project = _import(tmp_path, source, password)
    language = product_language()
    prepare_project_model(project)

    model = _normalized(
        build_project_model(project, tool_version=TOOL, language_code=language)
    )
    expected = _normalized(
        XKNXProj(source, password=password, language=language).parse()
    )
    for data in (model, expected):
        for device in data["devices"].values():
            device.pop("hardware_name")
            device.pop("name")
    assert _differences(model, expected) == []


def test_edits_show_in_the_next_model(tmp_path: Path) -> None:
    project = _import(tmp_path, _fixture())
    addresses = {ga.address: ga.id for ga in project.group_addresses}
    device = project.devices[0]
    unlinked = next(
        co
        for co in device.get_visible_com_objects()
        if co.db_id is not None and not project.get_links_for_com_object(co.db_id)
    )
    assert unlinked.db_id is not None

    project.rename_group_address(addresses["1/0/0"], "Renamed")
    project.set_group_address_dpt(addresses["1/0/5"], "DPST-1-8")
    project.link_com_object_to_ga(unlinked.db_id, addresses["2/0/6"], is_sending=True)
    model = build_project_model(project, tool_version=TOOL)

    assert model["group_addresses"]["1/0/0"]["name"] == "Renamed"
    assert model["group_addresses"]["1/0/5"]["dpt"] == {"main": 1, "sub": 8}
    key = f"1.1.1/{unlinked.id.split('_', 2)[2]}"
    assert model["communication_objects"][key]["group_address_links"] == ["2/0/6"]
    assert key in model["group_addresses"]["2/0/6"]["communication_object_ids"]
    assert key in model["devices"]["1.1.1"]["communication_object_ids"]


def test_a_device_added_in_the_editor_gets_its_texts_from_the_program(
    tmp_path: Path,
) -> None:
    project = _import(tmp_path, _fixture())
    source = project.model_source()
    assert source is not None
    row = source.devices[0]
    template = project.devices[0]
    node_id = project.add_device(
        row.product_ref_id,
        row.hardware2program_ref_id,
        "Added",
        template.app,
        segment_id=row.segment_id,
        address=2,
    )
    assert node_id is not None
    added = project.find_device_by_node_id(node_id)
    assert added is not None
    co = next(
        co
        for co in added.get_visible_com_objects()
        if co.db_id is not None and co.id.endswith("_O-4_R-1417")
    )
    assert co.db_id is not None
    addresses = {ga.address: ga.id for ga in project.group_addresses}
    project.link_com_object_to_ga(co.db_id, addresses["2/0/6"], is_sending=True)

    model = build_project_model(project, tool_version=TOOL)

    assert model["devices"]["1.1.2"]["name"] == "Added"
    assert "1.1.2" in model["topology"]["1"]["lines"]["1"]["devices"]
    com_object = model["communication_objects"]["1.1.2/O-4_R-1417"]
    expected = model["communication_objects"]["1.1.1/O-4_R-1417"]
    for key in ("name", "text", "function_text", "number", "object_size", "flags"):
        assert com_object[key] == expected[key]
    # The imported object sets its own datapoint type; this one has the program's object size.
    assert com_object["dpts"] == [{"main": 1, "sub": None}]


def test_no_project_no_model(tmp_path: Path) -> None:
    project = ProjectService(CatalogService(tmp_path / "c.xknxcatalog"))
    project.set_logger(Logger(LogService(), "project"))
    with pytest.raises(ProjectModelUnavailable):
        build_project_model(project, tool_version=TOOL)


def test_semantics_of_references_and_channels_are_read() -> None:
    program = SimpleNamespace(
        id="M-0001_A-0001-01-0000",
        static=SimpleNamespace(
            com_object_table=None,
            allocators=None,
            com_object_refs=SimpleNamespace(
                com_object_ref=[
                    ComObjectRef(
                        id="M-0001_A-0001-01-0000_O-1_R-1",
                        ref_id="M-0001_A-0001-01-0000_O-1",
                        semantics="knx:dpa.417.52 knx:dpa.417.51 knx:dpa.417.52",
                    ),
                    ComObjectRef(
                        id="M-0001_A-0001-01-0000_O-2_R-2",
                        ref_id="M-0001_A-0001-01-0000_O-2",
                    ),
                ]
            ),
        ),
        dynamic=[
            ApplicationProgramChannel(
                id="M-0001_A-0001-01-0000_CH-1",
                name="Light",
                number="1",
                text="Light {{0}}",
                semantics="knx:fb.417",
            )
        ],
        module_defs=None,
    )

    digest = digest_program(program)  # pyright: ignore[reportArgumentType]

    refs = digest.refs
    assert refs["M-0001_A-0001-01-0000_O-1_R-1"].dpas == ("417.52", "417.51")
    assert refs["M-0001_A-0001-01-0000_O-2_R-2"].dpas is None
    channel = digest.channels["M-0001_A-0001-01-0000_CH-1"]
    assert channel.functional_blocks == ("417",)
    assert channel.text == "Light {{0}}"


# --- the builder on rows of its own ---------------------------------------------------------

_APP = "M-0001_A-0001-01-0000"


def _object(**values: Any) -> ObjectDef:
    defaults: dict[str, Any] = {
        "name": None,
        "text": None,
        "function_text": None,
        "object_size": "1 Bit",
        "flags": (None,) * 6,
        "datapoint_types": "",
    }
    return ObjectDef(**{**defaults, **values})


def _com_object(
    id: int, instance: str, *, channel: str | None = None, ga: int = 1
) -> Any:
    return SimpleNamespace(
        id=id,
        device_id=1,
        ref_id=f"{_APP}_{instance}",
        instance_ref_id=instance,
        channel_id=channel,
        read_flag=None,
        write_flag=True,
        communication_flag=True,
        transmit_flag=None,
        update_flag=None,
        read_on_init_flag=None,
        text_override=None,
        function_text_override=None,
        description_override=None,
        datapoint_type_override=None,
        links=(SimpleNamespace(id=id, group_address_id=ga, is_sending=True),),
    )


def _source(**changes: Any) -> Any:
    device = SimpleNamespace(
        id=1,
        segment_id=1,
        address=7,
        name="Dimmer",
        product_name="Dimming actuator",
        order_number="",
        description="",
        manufacturer_name="",
        product_ref_id="M-0001_H-1_P-1",
        hardware2program_ref_id="M-0001_H-1_HP-1",
        application_program_ref_id=_APP,
        puid=None,
        space_id=None,
        serial_number="",
        last_download=None,
        individual_address_loaded=True,
        application_program_loaded=True,
        communication_part_loaded=True,
        medium_config_loaded=True,
        parameters_loaded=True,
        group_object_tree=(
            '<GroupObjectTree><Nodes><Node Type="Channel" RefId="CH-1" '
            'GroupObjectInstances="O-1_R-1" /></Nodes></GroupObjectTree>'
        ),
        module_instances=[
            SimpleNamespace(
                instance_id="MD-1_M-1_MI-2",
                ref_id="MD-1_M-1",
                arguments=[{"ref_id": "MD-1_A-1", "value": "B"}],
            )
        ],
    )
    values: dict[str, Any] = {
        "project": SimpleNamespace(
            id="P-0001",
            original_project_id="",
            name="Home",
            last_modified="",
            group_address_style="ThreeLevel",
            guid="guid",
            schema_version="23",
        ),
        "areas": [SimpleNamespace(id=1, address=1, name="Area", description="")],
        "lines": [
            SimpleNamespace(id=1, area_id=1, address=1, name="Line", description="")
        ],
        "segments": [SimpleNamespace(id=1, line_id=1, number=0, medium_type="MT-0")],
        "devices": [device],
        "com_objects": [
            _com_object(1, "O-1_R-1", channel="CH-1"),
            _com_object(2, "O-2_R-2", channel="CH-1"),
            _com_object(3, "MD-1_M-1_MI-2_O-3-0_R-3", channel="MD-1_M-1_MI-2_CH-2"),
        ],
        "parameters": {1: {f"{_APP}_MD-1_M-1_MI-2_P-4_R-4": "Hall"}},
        "group_ranges": [],
        "group_addresses": [
            SimpleNamespace(
                id=1,
                group_range_id=1,
                address=2049,
                name="Secure",
                datapoint_type=None,
                description="",
                comment="",
                puid=None,
                source_id="",
            )
        ],
        "spaces": [],
        "functions": [],
        "keyed": {1},
        "trades": [],
    }
    values.update(changes)
    return SimpleNamespace(**values)


_DIGEST = ApplicationDigest(
    application_id=_APP,
    objects={
        f"{_APP}_O-1": _object(name="Switch", text="Output", number=1),
        f"{_APP}_O-2": _object(name="Status", text="Output", number=2),
        f"{_APP}_MD-1_O-3-0": _object(
            name="Button", text="Button {{ChNo}}", number=1, base_number="A-2"
        ),
    },
    refs={
        f"{_APP}_O-1_R-1": _object(ref_id=f"{_APP}_O-1", dpas=("418.62",)),
        f"{_APP}_O-2_R-2": _object(ref_id=f"{_APP}_O-2"),
        f"{_APP}_MD-1_O-3-0_R-3": _object(
            ref_id=f"{_APP}_MD-1_O-3-0",
            text="{{0:Button}} {{ChNo}}",
            text_parameter_ref_id=f"{_APP}_MD-1_P-4_R-4",
        ),
    },
    channels={
        f"{_APP}_CH-1": ChannelDef(
            name="Channel A",
            text="Dimmer A",
            text_parameter_ref_id=None,
            functional_blocks=("418",),
        ),
        f"{_APP}_MD-1_CH-2": ChannelDef(
            name="Buttons",
            text="Buttons {{ChNo}}",
            text_parameter_ref_id=None,
            functional_blocks=None,
        ),
    },
    arguments={
        f"{_APP}_MD-1_A-1": ArgumentDef(name="ChNo", allocates=None, computed=False)
    },
)


def _build(source: Any) -> Any:
    builder = _Builder(source, {1: _DIGEST}, {3: 21})
    return builder.build(tool_version=TOOL, language_code=None)


def test_a_channel_holds_the_objects_its_tree_lists_and_those_stored_for_it() -> None:
    model = _build(_source())

    channels = model["devices"]["1.1.7"]["channels"]
    assert channels["CH-1"]["name"] == "Dimmer A"
    assert channels["CH-1"]["functional_blocks"] == ["418"]
    assert channels["CH-1"]["communication_object_ids"] == [
        "1.1.7/O-1_R-1",
        "1.1.7/O-2_R-2",
    ]
    assert channels["MD-1_M-1_MI-2_CH-2"]["name"] == "Buttons B"


def test_a_device_without_a_stored_tree_gets_its_channels_from_its_objects() -> None:
    source = _source()
    source.devices[0].group_object_tree = ""

    channels = _build(source)["devices"]["1.1.7"]["channels"]

    assert channels["CH-1"]["communication_object_ids"] == [
        "1.1.7/O-1_R-1",
        "1.1.7/O-2_R-2",
    ]
    assert channels["MD-1_M-1_MI-2_CH-2"]["communication_object_ids"] == [
        "1.1.7/MD-1_M-1_MI-2_O-3-0_R-3"
    ]


def test_an_object_of_a_module_instance_takes_its_arguments_and_text_parameter() -> (
    None
):
    model = _build(_source())

    com_object = model["communication_objects"]["1.1.7/MD-1_M-1_MI-2_O-3-0_R-3"]
    assert com_object["text"] == "Hall B"
    assert com_object["name"] == "Button"
    assert com_object["number"] == 21
    assert com_object["module_def"] == {"definition": "MD-1", "root_number": 1}
    assert com_object["text_parameter"] == {
        "identifier": "MD-1_M-1_MI-2_P-4_R-4",
        "value": "Hall",
    }
    assert com_object["dpts"] == [{"main": 1, "sub": None}]


def test_an_object_takes_the_semantics_of_its_reference() -> None:
    model = _build(_source())

    assert model["communication_objects"]["1.1.7/O-1_R-1"]["dpas"] == ["418.62"]
    assert model["communication_objects"]["1.1.7/O-2_R-2"]["dpas"] is None


def test_an_object_of_a_submodule_instance_is_of_the_submodule_definition() -> None:
    # A submodule instance is named after the instance it is in: the object belongs to the
    # innermost instance its id names, whatever their order.
    room, display = "MD-1_M-1_MI-2", "MD-1_M-1_MI-2_SM-1_M-1_MI-2-1"
    for order in ([room, display], [display, room]):
        context = SimpleNamespace(module_arguments=[(i, []) for i in order])
        definition = _Builder._module_definition  # pyright: ignore[reportPrivateUsage]
        assert definition(context, f"{display}_SM-1_O-3-0_R-1") == "MD-1_SM-1"  # type: ignore[arg-type]
        assert definition(context, f"{room}_O-3-0_R-3") == "MD-1"  # type: ignore[arg-type]


def test_a_group_address_with_a_key_is_data_secure() -> None:
    model = _build(_source())
    assert model["group_addresses"]["1/0/1"]["data_secure"] is True
    assert model["group_addresses"]["1/0/1"]["identifier"] == "GA-x1"

    model = _build(_source(keyed=set()))
    assert model["group_addresses"]["1/0/1"]["data_secure"] is False


def test_objects_the_tree_does_not_list_go_to_their_stored_channel() -> None:
    # O-1_R-1 is in a folder directly under the device: in no channel. O-2_R-2 is in no node of
    # the tree: a channel node of its own. O-3 goes into its channel's empty node in the folder.
    source = _source()
    source.devices[0].group_object_tree = (
        '<GroupObjectTree><Nodes><Node Type="Folder" RefId="PB-1" Text="General" '
        'GroupObjectInstances="O-1_R-1"><Nodes>'
        '<Node Type="Channel" RefId="MD-1_M-1_MI-2_CH-2" />'
        "</Nodes></Node></Nodes></GroupObjectTree>"
    )
    source.com_objects[0].channel_id = None

    model = _build(source)

    device = model["devices"]["1.1.7"]
    assert device["group_object_tree"] == [
        {
            "type": "Folder",
            "identifier": "PB-1",
            "name": "General",
            "communication_object_ids": ["1.1.7/O-1_R-1"],
            "nodes": [
                {
                    "type": "Channel",
                    "identifier": "MD-1_M-1_MI-2_CH-2",
                    "name": "Buttons B",
                    "communication_object_ids": ["1.1.7/MD-1_M-1_MI-2_O-3-0_R-3"],
                    "nodes": [],
                }
            ],
        },
        {
            "type": "Channel",
            "identifier": "CH-1",
            "name": "Dimmer A",
            "communication_object_ids": ["1.1.7/O-2_R-2"],
            "nodes": [],
        },
    ]
    assert list(device["channels"]) == ["MD-1_M-1_MI-2_CH-2", "CH-1"]
    objects = model["communication_objects"]
    assert objects["1.1.7/O-1_R-1"]["channel"] is None
    assert objects["1.1.7/O-2_R-2"]["channel"] == "CH-1"


def test_a_folder_is_named_by_its_parameter_block() -> None:
    source = _source()
    source.devices[0].group_object_tree = (
        '<GroupObjectTree><Nodes><Node Type="Channel" RefId="CH-1"><Nodes>'
        '<Node Type="Folder" RefId="MD-1_M-1_MI-2_PB-5" GroupObjectInstances="O-1_R-1" />'
        '<Node Type="Folder" RefId="PB-6" GroupObjectInstances="O-2_R-2" />'
        "</Nodes></Node></Nodes></GroupObjectTree>"
    )
    digest = dataclasses.replace(
        _DIGEST,
        blocks={
            f"{_APP}_MD-1_PB-5": BlockDef(
                name="Keys",
                text="Keys {{ChNo}}: {{0:none}}",
                text_parameter_ref_id=f"{_APP}_MD-1_P-4_R-4",
            ),
            # Only a text parameter that is not set: the block's name.
            f"{_APP}_PB-6": BlockDef(
                name="Spare", text="{{0}}", text_parameter_ref_id=None
            ),
        },
    )

    model = _Builder(source, {1: digest}, {3: 21}).build(
        tool_version=TOOL, language_code=None
    )

    channel, module_channel = model["devices"]["1.1.7"]["group_object_tree"]
    assert [node["name"] for node in channel["nodes"]] == ["Keys B: Hall", "Spare"]
    assert module_channel["identifier"] == "MD-1_M-1_MI-2_CH-2"
    # The objects of its folders are the channel's.
    assert model["devices"]["1.1.7"]["channels"]["CH-1"][
        "communication_object_ids"
    ] == ["1.1.7/O-1_R-1", "1.1.7/O-2_R-2"]


def _allocating(**changes: Any) -> ApplicationDigest:
    """:data:`_DIGEST` with ``ChNo`` drawn from the allocator ``L-1`` (start 100, 10 each)."""
    argument: dict[str, Any] = {
        "name": "ChNo",
        "allocates": 10,
        "computed": True,
        "allocator": True,
    }
    return dataclasses.replace(
        _DIGEST,
        arguments={f"{_APP}_MD-1_A-1": ArgumentDef(**{**argument, **changes})},
        allocators={f"{_APP}_L-1": 100},
        allocator_modules={f"{_APP}_L-1": 1},
    )


def _arguments(
    digest: ApplicationDigest, value: str, instance: str = "MD-1_M-1_MI-3"
) -> dict[str, str]:
    context = _DeviceContext(
        row=SimpleNamespace(),  # type: ignore[arg-type]
        ia="1.1.7",
        application=_APP,
        digest=digest,
        parameters={},
        module_arguments=[(instance, [(f"{_APP}_MD-1_A-1", value)])],
    )
    return context.arguments_for(f"{instance}_O-3-0_R-3")


def test_an_allocated_module_argument_takes_its_instance_s_value() -> None:
    # The third instance: start + 2 * Allocates; a stored number as it is.
    assert _arguments(_allocating(), "L-1") == {"ChNo": "120"}
    assert _arguments(_allocating(), "42") == {"ChNo": "42"}
    source = _source()
    source.devices[0].module_instances[0].arguments[0]["value"] = "L-1"
    model = _Builder(source, {1: _allocating()}, {3: 21}).build(
        tool_version=TOOL, language_code=None
    )
    text = model["communication_objects"]["1.1.7/MD-1_M-1_MI-2_O-3-0_R-3"]["text"]
    assert text == "Hall 110"


def test_a_module_argument_without_an_allocated_value_keeps_its_placeholder() -> None:
    # Several modules draw from the allocator, an unknown allocator, an offset to a base
    # value, an argument of a submodule.
    several = dataclasses.replace(_allocating(), allocator_modules={f"{_APP}_L-1": 2})
    assert _arguments(several, "L-1") == {}
    assert _arguments(_allocating(), "L-2") == {}
    assert _arguments(_allocating(base_value=True), "L-1") == {}
    assert _arguments(_allocating(base_value=True, allocator=False), "7") == {}
    assert _arguments(_allocating(), "L-1", "MD-1_M-1_MI-3_SM-1_M-1_MI-3-1") == {}
    assert _arguments(_allocating(allocates=None), "L-1") == {}


def test_trades_of_the_rows() -> None:
    def trade(id: int, parent: int | None, order: int, *devices: int) -> Any:
        return SimpleNamespace(
            id=id,
            parent_id=parent,
            order=order,
            source_id="" if id == 3 else f"P-0001-0_T-{id}",
            name=f"Trade {id}",
            number="",
            description="",
            puid=None,
            devices=[
                SimpleNamespace(id=i, device_id=d, order=i)
                for i, d in enumerate(devices)
            ],
        )

    # Trade 2 lists the device after its sub-trade 3 does; a device on no line is left out.
    model = _build(
        _source(trades=[trade(1, None, 1), trade(2, None, 0, 1, 9), trade(3, 2, 0, 1)])
    )

    assert [t["identifier"] for t in model["trades"]] == [
        "P-0001-0_T-2",
        "P-0001-0_T-1",
    ]
    assert model["trades"][0]["devices"] == ["1.1.7"]
    assert model["trades"][0]["trades"][0]["identifier"] == "T-x3"
    assert model["devices"]["1.1.7"]["trade_id"] == "T-x3"


def test_the_digest_holds_blocks_allocators_and_how_arguments_are_computed() -> None:
    module = SimpleNamespace(
        arguments=SimpleNamespace(
            argument=[
                SimpleNamespace(id=f"{_APP}_MD-1_A-1", name="ChNo", allocates=10),
                SimpleNamespace(id=f"{_APP}_MD-1_A-2", name="Base", allocates=None),
                SimpleNamespace(id=f"{_APP}_MD-1_A-3", name="Plain", allocates=None),
            ]
        ),
        static=SimpleNamespace(
            com_objects=None,
            com_object_refs=None,
            allocators=SimpleNamespace(
                allocator=[
                    Allocator(id=f"{_APP}_MD-1_L-2", name="M", start=5, max_inclusive=9)
                ]
            ),
        ),
        dynamic=[
            ModuleNumericArg(ref_id=f"{_APP}_MD-1_A-1", allocator_ref_id=f"{_APP}_L-1"),
            ModuleNumericArg(ref_id=f"{_APP}_MD-1_A-1", allocator_ref_id=f"{_APP}_L-1"),
            ModuleNumericArg(ref_id=f"{_APP}_MD-1_A-2", base_value="A-1"),
            ModuleNumericArg(ref_id=f"{_APP}_MD-1_A-3", value=3),
        ],
        sub_module_defs=None,
    )
    program = SimpleNamespace(
        id=_APP,
        static=SimpleNamespace(
            com_object_table=None,
            com_object_refs=None,
            allocators=SimpleNamespace(
                allocator=[
                    Allocator(id=f"{_APP}_L-1", name="L", start=100, max_inclusive=200)
                ]
            ),
        ),
        dynamic=[
            ComObjectParameterBlock(
                id=f"{_APP}_PB-1",
                name="Block",
                text="General",
                text_parameter_ref_id=f"{_APP}_P-1_R-1",
            )
        ],
        module_defs=SimpleNamespace(module_def=[module]),
    )

    digest = digest_program(program)  # pyright: ignore[reportArgumentType]

    assert digest.blocks == {
        f"{_APP}_PB-1": BlockDef(
            name="Block", text="General", text_parameter_ref_id=f"{_APP}_P-1_R-1"
        )
    }
    assert digest.allocators == {f"{_APP}_L-1": 100, f"{_APP}_MD-1_L-2": 5}
    assert digest.allocator_modules == {f"{_APP}_L-1": 2}
    chno, base, plain = (digest.arguments[f"{_APP}_MD-1_A-{i}"] for i in (1, 2, 3))
    assert (chno.computed, chno.allocator, chno.base_value) == (True, True, False)
    assert (base.computed, base.allocator, base.base_value) == (True, False, True)
    assert (plain.computed, plain.allocator, plain.base_value) == (False, False, False)
