"""MCP tools for the Tools window, bulk group addresses, the Mass Linker and plugin parameters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin

from .test_server import _FIXTURE, _build_ctx, _data

pytestmark = pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")


async def _error(client: Client, tool: str, match: str, **args: Any) -> None:
    with pytest.raises(Exception, match=match):
        await client.call_tool(tool, args)


def _server(tmp_path: Path, *, with_plugin: bool = True) -> tuple[Any, Any]:
    ctx = _build_ctx(tmp_path)
    if with_plugin:
        ctx.project_plugin = ProjectPlugin(ctx.api)
    return build_server(ctx), ctx.api.project


async def _device(client: Client, address: str | None = None) -> int:
    products = (await _data(client, "catalog_list_products"))["items"]
    if not products:
        await _data(client, "catalog_import_product", path=str(_FIXTURE))
        products = (await _data(client, "catalog_list_products"))["items"]
    node_id = (
        await _data(
            client, "project_add_device", product_ref_id=products[0]["product_ref_id"]
        )
    )["node_id"]
    if address is not None:
        await _data(
            client, "project_set_individual_address", node_id=node_id, address=address
        )
    return node_id


async def test_extended_copy_replace_and_shift(tmp_path: Path) -> None:
    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client, "1.1.10")
        await _data(client, "project_rename_device", node_id=node_id, name="Hall")
        copy = await _data(
            client,
            "project_extended_copy",
            node_id=node_id,
            count=2,
            find="Hall",
            replace="Kitchen",
            create_group_addresses=True,
        )
        assert copy["copied"] == 2
        assert len(copy["node_ids"]) == 2
        assert copy["errors"] == []
        names = [project.find_device_by_node_id(n).name for n in copy["node_ids"]]
        assert all(name.startswith("Kitchen") for name in names)
        assert project.group_addresses
        await _error(
            client, "project_extended_copy", "count must be", node_id=node_id, count=0
        )

        template = await _device(client, "1.1.20")
        replaced = await _data(
            client,
            "project_replace_device",
            node_id=node_id,
            template_node_id=template,
        )
        assert replaced["errors"] == []
        assert project.find_device_by_node_id(node_id) is None
        await _error(
            client,
            "project_replace_device",
            "cannot replace itself",
            node_id=template,
            template_node_id=template,
        )

        shifted = await _data(
            client, "project_shift_individual_addresses", node_ids=[template], offset=5
        )
        assert shifted == {"changed": 1, "errors": []}
        assert project.find_device_by_node_id(template).individual_address == "1.1.25"
        clash = await _data(
            client,
            "project_shift_individual_addresses",
            node_ids=[template],
            offset=300,
        )
        assert clash["changed"] == 0
        assert clash["errors"]


async def test_group_addresses_shift_create_and_mass_link(tmp_path: Path) -> None:
    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        ga = (await _data(client, "project_create_group_address", address="1/1/1"))[
            "group_address_id"
        ]
        shifted = await _data(
            client, "project_shift_group_addresses", group_address_ids=[ga], offset=4
        )
        assert shifted == {"changed": 1, "errors": []}
        assert project.get_group_address(ga).address == "1/1/5"

        source = await _device(client, "1.1.30")
        target = await _device(client, "1.1.31")
        objects = [
            co["ref_id"]
            for co in (await _data(client, "project_list_com_objects", node_id=source))[
                "items"
            ]
            if co["linkable"]
        ]
        created = await _data(
            client,
            "project_create_group_addresses_for_com_objects",
            node_id=source,
            com_object_ref_ids=objects[:2],
            start_address="2/0/1",
            name_template="Light {n}",
        )
        assert created["created"] == 2
        names = {
            project.get_group_address(g).name for g in created["group_address_ids"]
        }
        assert names == {"Light 1", "Light 2"}
        await _error(
            client,
            "project_create_group_addresses_for_com_objects",
            "no visible group objects",
            node_id=source,
            com_object_ref_ids=["nope"],
        )

        linked = await _data(
            client,
            "project_link_com_objects",
            pairs=[
                {
                    "source_node_id": source,
                    "source_com_object_ref_id": objects[2],
                    "target_node_id": target,
                    "target_com_object_ref_id": objects[2],
                    "address": "3/0/1",
                    "name": "Pair",
                }
            ],
        )
        assert linked == {"linked": 1, "errors": []}
        links = (await _data(client, "project_list_links"))["items"]
        pair = {
            link["node_id"]
            for link in links
            if project.get_group_address(link["group_address_id"]).address == "3/0/1"
        }
        assert pair == {source, target}
        await _error(
            client,
            "project_link_com_objects",
            "needs source_node_id",
            pairs=[{"source_node_id": source}],
        )


async def test_history_steps_carry_their_time(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    mcp, _project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _device(client, "1.1.40")
        steps = (await _data(client, "project_history"))["items"]
        assert steps
        for step in steps:
            assert step["timestamp"].endswith("Z")
            moment = datetime.fromisoformat(step["timestamp"])
            assert abs(datetime.now(UTC) - moment) < timedelta(minutes=5)
        newest = steps[0]["timestamp"]
        since = await _data(client, "project_history", since=newest)
        assert since["count"] >= 1
        assert all(s["timestamp"] >= newest for s in since["items"])
        later = (datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None)
        none = await _data(client, "project_history", since=later.isoformat())
        assert none["count"] == 0
        await _error(client, "project_history", "no ISO 8601", since="yesterday")


async def test_compare_projects_lists_what_changed_in_a_device(tmp_path: Path) -> None:
    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "old.xknx"))
        node_id = await _device(client, "1.1.40")
        params = (await _data(client, "project_list_parameters", node_id=node_id))[
            "items"
        ]
        enum = next(
            p
            for p in params
            if p["widget"].get("type") == "enum"
            and p["widget"].get("choice_count", 0) > 1
            and p.get("access") == "READ_WRITE"
        )
        full = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            parameter_ref_id=enum["ref_id"],
        )
        value = next(
            str(c["value"])
            for c in full["widget"]["choices"]
            if str(c["value"]) != str(enum["value"])
        )
        # Continue in a copy, so the old file keeps the device as it was.
        assert project.save_as(tmp_path / "new.xknx") is not None
        await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=enum["ref_id"],
            value=value,
        )
        await _data(
            client, "project_set_individual_address", node_id=node_id, address="1.1.41"
        )
        result = await _data(
            client, "project_compare_projects", path=str(tmp_path / "old.xknx")
        )
    # One device: the same one with another address, not one removed and one added.
    (device,) = result["devices"]
    assert (device["key"], device["kind"]) == ("1.1.41", "changed")
    assert "address '1.1.41' -> '1.1.40'" in device["detail"]
    labels = [p["label"] for p in device["parameters"] if p["kind"] == "changed"]
    assert any(enum["label"] in label for label in labels)


async def test_compare_projects(tmp_path: Path) -> None:
    mcp, _project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _device(client, "1.1.40")
        other = tmp_path / "other.xknx"
        missing = await _data(client, "project_compare_projects", path=str(other))
        assert missing["error"]
        assert missing["needs_password"] is False
        same = await _data(
            client, "project_compare_projects", path=str(tmp_path / "p.xknx")
        )
        assert same == {"devices": [], "group_addresses": []}


async def test_plugin_parameters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from editor_gui import plugin_params

    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client)
        await _error(
            client,
            "project_plugin_parameters",
            "no plugin parameters",
            node_id=node_id,
        )

        # The fixture's application has no plugin; any mapping groups its parameters.
        mapping = plugin_params.mapping_for("M-0008_A-C030-21-AA50-O000A")
        monkeypatch.setattr(plugin_params, "mapping_for", lambda app_id: mapping)
        listed = await _data(client, "project_plugin_parameters", node_id=node_id)
        assert listed["title"] == mapping.title
        parameter = next(
            p
            for s in listed["sections"]
            for p in s["parameters"]
            if p["label"].startswith("Delay after bus voltage return")
        )
        assert parameter["writable"]
        other = "5"
        result = await _data(
            client,
            "project_set_plugin_parameter",
            node_id=node_id,
            ref_id=parameter["ref_id"],
            value=other,
        )
        assert result == {
            "ref_id": parameter["ref_id"],
            "value": other,
            "changed": True,
        }
        assert project.find_device_by_node_id(node_id) is not None
        await _error(
            client,
            "project_set_plugin_parameter",
            "no plugin parameter",
            node_id=node_id,
            ref_id="nope",
            value="1",
        )
        section = listed["sections"][0]["section"]
        await _error(
            client,
            "project_add_plugin_item",
            "is not a list",
            node_id=node_id,
            section=section,
        )
        await _error(
            client,
            "project_remove_plugin_item",
            "no plugin parameter section",
            node_id=node_id,
            section="nope",
            item_id="1",
        )


async def test_plugin_parameter_item_name_is_exposed_and_searchable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A BLOB row's item name lives in ``group`` (the label is just the field). The tool must
    # expose ``group`` and search it, so a client can still find a row by the item's name.
    from editor_gui import plugin_params

    mcp, _project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client)
        mapping = plugin_params.mapping_for("M-0008_A-C030-21-AA50-O000A")
        monkeypatch.setattr(plugin_params, "mapping_for", lambda app_id: mapping)
        section = plugin_params.PluginSectionView(
            section="DALI Groups",
            parameters=[
                plugin_params.PluginParameter(
                    ref_id="dali_backup:b:groups:2:description",
                    name="x",
                    label="Name",
                    value="Sauna",
                    default_value="",
                    section="",
                    display_order=0,
                    writable=True,
                    group="[2] Group 'Sauna'",
                    group_key="groups:2",
                )
            ],
        )
        monkeypatch.setattr(
            plugin_params, "collect_from_device", lambda device, m: [section]
        )
        listed = await _data(client, "project_plugin_parameters", node_id=node_id)
        row = listed["sections"][0]["parameters"][0]
        assert row["group"] == "[2] Group 'Sauna'"
        # Searching the item name keeps the row even though its label is only "Name".
        found = await _data(
            client, "project_plugin_parameters", node_id=node_id, search="sauna"
        )
        assert found["sections"] and found["sections"][0]["parameters"]


async def test_triton_display_setup_edit_and_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The triton LCD display config is seeded by project_setup_triton_display, then readable/
    # editable through the generic plugin-parameter tools. The fixture device is not a real triton,
    # so force the type and make the triton mapping apply to it.
    from editor_gui import plugin_params
    from xknxeditor.vendor.triton import DeviceType, from_json_bytes

    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client)
        triton = plugin_params._load_mapping(
            str(plugin_params._MAPPINGS_DIR / "busch_jaeger_triton.json")
        )
        monkeypatch.setattr(plugin_params, "mapping_for", lambda app_id: triton)

        setup = await _data(
            client,
            "project_setup_triton_display",
            node_id=node_id,
            device_type="triton-6325",
        )
        assert setup["ready"] and setup["device_type"] == "triton-6325"
        assert setup["lines"] == 5
        assert project.history()[0].display_text == "Set up LCD display"

        # The seeded config is stored in the project and is a 5-line triton display.
        device = project.find_device_by_node_id(node_id)
        assert device is not None
        cfg = from_json_bytes(project.device_binary_data(device, "TritonLcd-Config"))
        assert cfg.device_type is DeviceType.TRITON_6325 and len(cfg.lines) == 5

        # Setup is a no-op the second time (no duplicate undo churn).
        revision = project.revision
        again = await _data(
            client,
            "project_setup_triton_display",
            node_id=node_id,
            device_type="6136UP",
        )
        assert again == setup
        assert project.revision == revision

        # The lines are now readable and a field is editable through the generic tools.
        listed = await _data(client, "project_plugin_parameters", node_id=node_id)
        data_type_ref = next(
            p["ref_id"]
            for s in listed["sections"]
            for p in s["parameters"]
            if p["ref_id"].endswith(":line:1:data_type")
        )
        out = await _data(
            client,
            "project_set_plugin_parameter",
            node_id=node_id,
            ref_id=data_type_ref,
            value="eis1-switch",
        )
        assert out["changed"]
        revision = project.revision
        await _error(
            client,
            "project_set_plugin_parameter",
            "invalid",
            node_id=node_id,
            ref_id="triton_lcd:TritonLcd-Config:line:1:text_off",
            value="x" * 17,
        )
        assert project.revision == revision


async def test_import_triton_prj_matches_devices_by_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A real LVS 2 ".prj" is imported onto the project device whose individual address matches a
    # display's physical address; displays without a device are skipped.
    import shutil

    import xknxeditor.vendor as vendor
    from editor_gui import plugin_params

    fixture = (
        Path(vendor.__file__).resolve().parents[3]
        / "tests"
        / "fixtures"
        / "sample_triton.prj"
    )
    prj = tmp_path / "copy.prj"
    shutil.copy(fixture, prj)

    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        # Make line 15.15 exist so the fixture's display address can be assigned.
        project.find_or_create_segment_for_address("15.15.255")
        node_id = await _device(client, "15.15.255")
        triton = plugin_params._load_mapping(
            str(plugin_params._MAPPINGS_DIR / "busch_jaeger_triton.json")
        )
        monkeypatch.setattr(plugin_params, "mapping_for", lambda app_id: triton)

        history_before = len(project.history())
        result = await _data(client, "project_import_triton_prj", path=str(prj))
        assert result["imported"] == ["15.15.255"]
        assert project.history()[0].display_text == "Import LCD display"
        assert len(project.history()) == history_before + 1
        revision = project.revision
        again = await _data(client, "project_import_triton_prj", path=str(prj))
        assert again["imported"] == []
        assert project.revision == revision
        # The fixture's 1.1.21/1.1.23 displays have no device at their address.
        assert any(
            entry["address"] in {"1.1.21", "1.1.23"} for entry in result["skipped"]
        )

        # The imported config is now exposed as LCD lines (a group per line).
        listed = await _data(client, "project_plugin_parameters", node_id=node_id)
        line_groups = {
            p.get("group")
            for s in listed["sections"]
            for p in s["parameters"]
            if ":line:" in p["ref_id"] and p.get("group")
        }
        assert len(line_groups) == 5
        edited = await _data(
            client,
            "project_set_plugin_parameter",
            node_id=node_id,
            ref_id="triton_lcd:TritonLcd-Config:device:name",
            value="Imported",
        )
        assert edited["changed"] and edited["value"] == "Imported"
        assert project.undo()
        assert project.undo()
        device = project.find_device_by_node_id(node_id)
        assert project.device_binary_data(device, "TritonLcd-Config") is None
        assert project.redo()
        device = project.find_device_by_node_id(node_id)
        assert project.device_binary_data(device, "TritonLcd-Config") is not None


async def test_apply_triton_prj_to_device_targets_that_device(
    tmp_path: Path,
) -> None:
    # The device setup area imports one display onto the device being viewed (not a project-wide
    # address match): the display at the device's address, else the sole display in the file.
    import xknxeditor.vendor as vendor
    from xknxeditor.vendor.triton import PrjError

    fixtures = Path(vendor.__file__).resolve().parents[3] / "tests" / "fixtures"
    multi = (fixtures / "sample_triton.prj").read_bytes()
    single = (fixtures / "sample_triton_map.prj").read_bytes()

    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        project.find_or_create_segment_for_address("15.15.255")
        project.find_or_create_segment_for_address("2.2.2")
        node_id = await _device(client, "15.15.255")
        other = await _device(client, "2.2.2")
        device = project.find_device_by_node_id(node_id)
        history_before = len(project.history())
        # Address match: the fixture's 15.15.255 display lands on this device.
        assert project.apply_triton_prj_to_device(device, multi) == "15.15.255"
        assert len(project.history()) == history_before + 1
        device = project.find_device_by_node_id(node_id)
        assert project.device_binary_data(device, "TritonLcd-Config") is not None
        # Re-applying the same file is a no-op (byte-identical, no undo step).
        revision = project.revision
        assert project.apply_triton_prj_to_device(device, multi) is None
        assert project.revision == revision

        # Several displays and none matches this device's address -> a clear error, not silence.
        other_device = project.find_device_by_node_id(other)
        with pytest.raises(PrjError, match="none matches"):
            project.apply_triton_prj_to_device(other_device, multi)
        assert project.device_binary_data(other_device, "TritonLcd-Config") is None

        # A single-display file applies to the viewed device even when its address differs; the
        # stored config carries the target device's address.
        applied = project.apply_triton_prj_to_device(other_device, single)
        assert applied == "2.2.2"
        other_device = project.find_device_by_node_id(other)
        stored = project.device_binary_data(other_device, "TritonLcd-Config")
        assert stored is not None
        from xknxeditor.vendor.triton import from_json_bytes

        assert from_json_bytes(stored).physical_address == "2.2.2"


async def test_apply_triton_prj_rejects_mismatched_display_type(tmp_path: Path) -> None:
    # A ".prj" display whose type contradicts the target device's detected type is refused
    # (device-targeted) or skipped (bulk), so a triton is never configured as a 6136UP.
    from xknxeditor.vendor.triton import (
        DeviceType,
        DisplayDevice,
        PrjError,
        blank_prj,
    )

    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        project.find_or_create_segment_for_address("1.1.9")
        node_id = await _device(client, "1.1.9")
        await _data(
            client, "project_rename_device", node_id=node_id, name="triton 6325 LCD"
        )
        device = project.find_device_by_node_id(node_id)
        detected = project.triton_device_type(device)
        assert detected is not None and detected.value == "triton-6325"

        up = blank_prj(
            [
                DisplayDevice(
                    device_type=DeviceType.INTERFACE_6136UP, physical_address="1.1.9"
                )
            ]
        )
        with pytest.raises(PrjError, match="6136UP"):
            project.apply_triton_prj_to_device(device, up)
        assert project.device_binary_data(device, "TritonLcd-Config") is None

        summary = project.import_triton_prj(up)
        assert summary["imported"] == []
        assert summary["skipped"] == [
            {"address": "1.1.9", "reason": "display type does not match the device"}
        ]


async def test_export_triton_prj_round_trips_the_stored_config(
    tmp_path: Path,
) -> None:
    # A configured display exports to a ".prj" that re-reads to the same display; exporting a
    # device with no triton config is an error.
    import xknxeditor.vendor as vendor
    from xknxeditor.vendor.triton import read_prj

    fixtures = Path(vendor.__file__).resolve().parents[3] / "tests" / "fixtures"
    single = (fixtures / "sample_triton_map.prj").read_bytes()
    source = read_prj(single)[0]

    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        project.find_or_create_segment_for_address("1.1.1")
        node_id = await _device(client, "1.1.1")

        out = tmp_path / "exported.prj"
        await _error(
            client,
            "project_export_triton_prj",
            "no triton LCD configuration",
            node_id=node_id,
            path=str(out),
        )

        device = project.find_device_by_node_id(node_id)
        project.apply_triton_prj_to_device(device, single)
        result = await _data(
            client, "project_export_triton_prj", node_id=node_id, path=str(out)
        )
        assert result["path"] == str(out)
        assert result["bytes"] > 0
        reread = read_prj(out.read_bytes())
        assert len(reread) == 1
        got = reread[0]
        # The exported file carries this device's address and the source display's line content.
        assert got.physical_address == "1.1.1"
        assert got.device_type == source.device_type
        n = source.device_type.max_lines
        assert [(ln.data_type, ln.text_off, ln.text_on) for ln in got.lines[:n]] == [
            (ln.data_type, ln.text_off, ln.text_on) for ln in source.lines[:n]
        ]


async def test_setup_triton_display_rejects_unknown_type(tmp_path: Path) -> None:
    mcp, _project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client)
        await _error(
            client,
            "project_setup_triton_display",
            "unknown device_type",
            node_id=node_id,
            device_type="nonsense",
        )


async def test_the_actions_need_the_project_plugin(tmp_path: Path) -> None:
    mcp, _project = _server(tmp_path, with_plugin=False)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _error(
            client,
            "project_shift_group_addresses",
            "not available",
            group_address_ids=[],
            offset=1,
        )


async def test_triton_setup_long_name_is_refused_not_crashing(tmp_path: Path) -> None:
    # A device name longer than the display allows makes setup validation fail; the GUI handler
    # reports it as a refusal instead of raising an uncaught exception.
    from editor_gui.plugin_blob_readers.triton_lcd import BLOB_NAME

    ctx = _build_ctx(tmp_path)
    plugin = ProjectPlugin(ctx.api)
    ctx.project_plugin = plugin
    project = ctx.api.project
    refused: list[str] = []
    ctx.api.warn = lambda msg: refused.append(msg)
    async with Client(build_server(ctx)) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client)
        await _data(
            client,
            "project_rename_device",
            node_id=node_id,
            name="triton 6325 " + "x" * 40,
        )
        device = project.find_device_by_node_id(node_id)
        plugin._handle_setup_display(device, "triton-6325")  # must not raise
        assert refused
        assert project.device_binary_data(device, BLOB_NAME) is None


async def test_triton_import_dropped_when_project_swapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A ".prj" picker left open while the project is swapped must not write the stale import into
    # the new project's device at the same node id.
    from editor_gui import file_dialogs
    from editor_gui.plugin_blob_readers.triton_lcd import BLOB_NAME
    from xknxeditor.vendor.triton import DeviceType, DisplayDevice, blank_prj

    class _ReadyPick:
        def __init__(self, paths: list[str]) -> None:
            self._paths = paths

        def ready(self, timeout: int = 0) -> bool:
            return True

        def result(self) -> list[str]:
            return self._paths

    prj = tmp_path / "x.prj"
    prj.write_bytes(
        blank_prj(
            [
                DisplayDevice(
                    device_type=DeviceType.TRITON_6325, physical_address="1.1.1"
                )
            ]
        )
    )

    ctx = _build_ctx(tmp_path)
    plugin = ProjectPlugin(ctx.api)
    ctx.project_plugin = plugin
    project = ctx.api.project
    async with Client(build_server(ctx)) as client:
        await _data(client, "project_new", path=str(tmp_path / "a.xknx"))
        project.find_or_create_segment_for_address("1.1.1")
        node_id = await _device(client, "1.1.1")
        device = project.find_device_by_node_id(node_id)
        monkeypatch.setattr(
            file_dialogs, "open_file", lambda *a, **k: _ReadyPick([str(prj)])
        )
        plugin._handle_import_prj(device)  # captures project A's generation

        # Swap to a fresh project and give it a device at the same address.
        await _data(client, "project_new", path=str(tmp_path / "b.xknx"))
        project.find_or_create_segment_for_address("1.1.1")
        node_b = await _device(client, "1.1.1")
        plugin._poll_triton_prj_dialog()  # stale: generation changed -> no write
        device_b = project.find_device_by_node_id(node_b)
        assert project.device_binary_data(device_b, BLOB_NAME) is None


async def test_triton_first_edit_atomic_and_undoable(tmp_path: Path) -> None:
    from editor_gui.plugin_blob_readers.triton_lcd import BLOB_NAME
    from xknxeditor.vendor.triton import from_json_bytes

    ctx = _build_ctx(tmp_path)
    plugin = ProjectPlugin(ctx.api)
    ctx.project_plugin = plugin
    project = ctx.api.project
    async with Client(build_server(ctx)) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client)
        await _data(
            client, "project_rename_device", node_id=node_id, name="triton 6325"
        )
        device = project.find_device_by_node_id(node_id)
        assert device is not None
        revision = project.revision
        history_before = len(project.history())
        plugin.handle_param_change(
            device, f"triton_lcd:{BLOB_NAME}:device:beep_on_keypress", "false"
        )
        plugin.handle_param_change(device, "triton_lcd:Other:device:name", "Wrong")
        plugin.handle_param_change(
            device, f"triton_lcd:{BLOB_NAME}:device:name", device.name
        )
        assert project.device_binary_data(device, BLOB_NAME) is None
        assert project.revision == revision
        plugin.handle_param_change(
            device, f"triton_lcd:{BLOB_NAME}:line:1:data_type", "eis1-switch"
        )
        original_device = device
        device = project.find_device_by_node_id(node_id)
        blob = project.device_binary_data(device, BLOB_NAME)
        assert (
            blob is not None
            and from_json_bytes(blob).lines[0].data_type.value == "eis1-switch"
        )
        assert len(project.history()) == history_before + 1
        assert project.history()[0].display_text == "Edit LCD display"
        revision = project.revision
        plugin.handle_param_change(
            device, f"triton_lcd:{BLOB_NAME}:line:1:data_type", "eis1-switch"
        )
        assert project.revision == revision
        assert project.undo()
        assert (
            project.device_binary_data(
                project.find_device_by_node_id(node_id), BLOB_NAME
            )
            is None
        )
        assert project.redo()
        assert (
            project.device_binary_data(
                project.find_device_by_node_id(node_id), BLOB_NAME
            )
            == blob
        )

        plugin.handle_param_change(
            original_device, f"triton_lcd:{BLOB_NAME}:device:name", "Updated"
        )
        current = project.find_device_by_node_id(node_id)
        config = from_json_bytes(project.device_binary_data(current, BLOB_NAME))
        assert config.name == "Updated"
        assert config.lines[0].data_type.value == "eis1-switch"


async def test_triton_setup_without_mapping_does_not_persist(tmp_path: Path) -> None:
    from editor_gui.plugin_blob_readers.triton_lcd import BLOB_NAME

    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client)
        revision = project.revision
        await _error(
            client,
            "project_setup_triton_display",
            "no plugin parameters",
            node_id=node_id,
            device_type="triton-6325",
        )
        assert project.revision == revision
        assert (
            project.device_binary_data(
                project.find_device_by_node_id(node_id), BLOB_NAME
            )
            is None
        )


async def test_triton_import_errors_do_not_persist(tmp_path: Path) -> None:
    import xknxeditor.vendor as vendor
    from xknxeditor.vendor.triton import read_prj, write_prj

    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _device(client, "1.1.21")
        revision = project.revision
        await _error(
            client,
            "project_import_triton_prj",
            "cannot read",
            path=str(tmp_path / "missing.prj"),
        )
        bad = tmp_path / "bad.prj"
        bad.write_bytes(b"bad")
        await _error(client, "project_import_triton_prj", "not a valid", path=str(bad))
        fixture = (
            Path(vendor.__file__).resolve().parents[3]
            / "tests/fixtures/sample_triton.prj"
        )
        original = fixture.read_bytes()
        displays = read_prj(original)
        displays[-1].lines[0].text_off = "x" * 17
        bad.write_bytes(write_prj(displays, original))
        await _error(client, "project_import_triton_prj", "not a valid", path=str(bad))
        assert project.revision == revision


async def test_triton_map_import_is_editable_and_retains_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import xknxeditor.vendor as vendor
    from editor_gui import plugin_params
    from editor_gui.plugin_blob_readers.triton_lcd import BLOB_NAME
    from xknxeditor.vendor.triton import from_json_bytes, read_prj

    fixture = (
        Path(vendor.__file__).resolve().parents[3]
        / "tests/fixtures/sample_triton_map.prj"
    )
    mapping = plugin_params._load_mapping(
        str(plugin_params._MAPPINGS_DIR / "busch_jaeger_triton.json")
    )
    monkeypatch.setattr(plugin_params, "mapping_for", lambda app_id: mapping)
    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client, "1.1.1")
        result = await _data(client, "project_import_triton_prj", path=str(fixture))
        assert result == {"imported": ["1.1.1"], "skipped": []}
        expected = read_prj(fixture.read_bytes())[0]
        expected.lines = expected.lines[:5]
        actual = project.device_binary_data(
            project.find_device_by_node_id(node_id), BLOB_NAME
        )
        assert from_json_bytes(actual) == expected
        result = await _data(
            client,
            "project_set_plugin_parameter",
            device_address="1.1.1",
            ref_id=f"triton_lcd:{BLOB_NAME}:line:1:text_off",
            value="Value",
        )
        assert result["changed"] and result["value"] == "Value"


async def test_triton_setup_rejects_invalid_existing_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from editor_gui import plugin_params
    from editor_gui.plugin_blob_readers.triton_lcd import BLOB_NAME

    mapping = plugin_params._load_mapping(
        str(plugin_params._MAPPINGS_DIR / "busch_jaeger_triton.json")
    )
    monkeypatch.setattr(plugin_params, "mapping_for", lambda app_id: mapping)
    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        node_id = await _device(client)
        device = project.find_device_by_node_id(node_id)
        project.persist_device_blob(device, BLOB_NAME, b"null")
        revision = project.revision
        await _error(
            client,
            "project_setup_triton_display",
            "not a triton LCD config",
            node_id=node_id,
        )
        assert project.revision == revision
        device = project.find_device_by_node_id(node_id)
        assert project.device_binary_data(device, BLOB_NAME) == b"null"


async def test_triton_import_skips_ambiguous_project_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    import xknxeditor.vendor as vendor

    fixture = (
        Path(vendor.__file__).resolve().parents[3]
        / "tests/fixtures/sample_triton_map.prj"
    )
    mcp, project = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        revision = project.revision
        devices = [
            SimpleNamespace(individual_address="1.1.1"),
            SimpleNamespace(individual_address="1.1.1"),
        ]
        monkeypatch.setattr(type(project), "devices", property(lambda _self: devices))
        result = await _data(client, "project_import_triton_prj", path=str(fixture))
        assert result == {
            "imported": [],
            "skipped": [{"address": "1.1.1", "reason": "ambiguous device address"}],
        }
        assert project.revision == revision
