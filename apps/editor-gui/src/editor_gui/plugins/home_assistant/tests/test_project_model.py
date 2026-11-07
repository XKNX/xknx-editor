"""The open project as Home Assistant's KNX integration reads it: the same as xknxproject gives
for the project file it was imported from, and what the editor changed since."""

from __future__ import annotations

import json
import os
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
    ChannelDef,
    ObjectDef,
    digest_program,
)
from editor_gui.plugins.home_assistant.project_model import (
    CREATED_BY,
    ProjectModelUnavailable,
    _Builder,  # pyright: ignore[reportPrivateUsage]
    build_project_model,
    prepare_project_model,
)
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.project.service import ProjectService
from xknxeditor.namespaces.intermediate.application_program_channel_t import (
    ApplicationProgramChannel,
)
from xknxeditor.namespaces.intermediate.com_object_ref_t import ComObjectRef

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


def test_a_group_address_with_a_key_is_data_secure() -> None:
    model = _build(_source())
    assert model["group_addresses"]["1/0/1"]["data_secure"] is True
    assert model["group_addresses"]["1/0/1"]["identifier"] == "GA-x1"

    model = _build(_source(keyed=set()))
    assert model["group_addresses"]["1/0/1"]["data_secure"] is False
