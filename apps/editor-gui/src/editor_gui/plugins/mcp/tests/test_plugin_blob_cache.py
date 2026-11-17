"""Blob edits refresh the shared parameter view and survive undo and reopening."""

from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui import plugin_params
from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.tests.test_powertool import current, native_project
from editor_gui.plugins.project.ui.plugin_params_panel import collection_item_id

from .test_server import _build_ctx, _data

FIXTURES = Path(__file__).parents[5] / "tests/fixtures"


async def test_dali_fade_choice_keeps_wire_code_through_mcp(tmp_path, monkeypatch):
    ctx = _build_ctx(tmp_path)
    project, node = native_project(tmp_path, monkeypatch, ctx.api.project)
    ctx.project_plugin = ProjectPlugin(ctx.api)
    mapping = plugin_params.PluginMapping((), "DALI", (), blob_reader="dali_backup")
    monkeypatch.setattr(plugin_params, "mapping_for_device", lambda _: mapping)
    name = "DaliGC16-Backup-Store"
    original = (FIXTURES / "dali_gc16_backup.dat").read_bytes()
    project.persist_device_blob(current(project, node), name, original)
    try:
        async with Client(build_server(ctx)) as client:
            listed = await _data(client, "project_plugin_parameters", node_id=node)
            row = next(
                p
                for s in listed["sections"]
                for p in s["parameters"]
                if p["ref_id"].endswith(":fadeTime")
            )
            assert {v["value"]: v["label"] for v in row["choices"]}["9"] == "10 s"
            await _data(
                client,
                "project_set_plugin_parameter",
                node_id=node,
                ref_id=row["ref_id"],
                value="9",
            )
            data = project.device_binary_data(current(project, node), name)
            from editor_gui.plugin_blob_readers.dali_backup import read

            changed = next(
                p
                for s in read(data, name)
                for p in s.parameters
                if p.ref_id == row["ref_id"]
            )
            assert changed.value == "9"
            project.undo()
            assert project.device_binary_data(current(project, node), name) == original
    finally:
        project.close()


@pytest.mark.parametrize(
    ("filename", "blob_name", "reader"),
    [
        ("siemens_n141_blob_para.dat", "BLOB_PARA", "siemens_dali"),
        ("dali_gc16_backup.dat", "DaliGC16-Backup-Store", "dali_backup"),
        ("dali_e64pro_backup.dat", "DaliE64Pro-Backup-Store", "dali_backup"),
    ],
)
async def test_blob_edits_refresh_gui_and_mcp(
    tmp_path, monkeypatch, filename, blob_name, reader
):
    ctx = _build_ctx(tmp_path)
    project, node_id = native_project(tmp_path, monkeypatch, ctx.api.project)
    plugin = ctx.project_plugin = ProjectPlugin(ctx.api)
    mapping = plugin_params.PluginMapping((), "DALI", (), blob_reader=reader)
    monkeypatch.setattr(plugin_params, "mapping_for_device", lambda _: mapping)
    original = (FIXTURES / filename).read_bytes()
    project.persist_device_blob(current(project, node_id), blob_name, original)

    def sections():
        return project.plugin_sections(current(project, node_id), mapping)

    initial = sections()
    parameter = next(
        p
        for section in initial
        for p in section.parameters
        if p.writable
        and (
            (p.ref_id.startswith("siemens_blob:") and p.value.isdigit())
            or (
                p.ref_id.startswith("dali_backup:")
                and p.ref_id.endswith(":description")
            )
        )
    )

    def value():
        return next(
            p.value
            for section in sections()
            for p in section.parameters
            if p.ref_id == parameter.ref_id
        )

    async with Client(build_server(ctx)) as client:
        await _data(client, "project_plugin_parameters", node_id=node_id)
        cursor = project.cursor
        changed_value = (
            str(int(parameter.value) + 1) if reader == "siemens_dali" else "Hall"
        )
        plugin.handle_param_change(
            current(project, node_id), parameter.ref_id, changed_value
        )
        assert project.cursor == cursor + 1
        assert value() == changed_value
        listed = await _data(client, "project_plugin_parameters", node_id=node_id)
        assert (
            next(
                p["value"]
                for s in listed["sections"]
                for p in s["parameters"]
                if p["ref_id"] == parameter.ref_id
            )
            == changed_value
        )
        next_value = (
            str(int(changed_value) + 1) if reader == "siemens_dali" else "Office"
        )
        result = await _data(
            client,
            "project_set_plugin_parameter",
            node_id=node_id,
            ref_id=parameter.ref_id,
            value=next_value,
        )
        assert result["changed"] and value() == next_value
        edited = project.device_binary_data(current(project, node_id), blob_name)
        project.undo()
        assert value() == changed_value
        project.undo()
        assert value() == parameter.value
        assert (
            project.device_binary_data(current(project, node_id), blob_name) == original
        )
        project.redo()
        project.redo()
        assert value() == next_value
        project.close()
        project.open(tmp_path / "project.xknx")
        assert value() == next_value
        assert (
            project.device_binary_data(current(project, node_id), blob_name) == edited
        )
    project.close()


async def test_collection_add_remove_refreshes_cache_and_is_undoable(
    tmp_path, monkeypatch
):
    ctx = _build_ctx(tmp_path)
    project, node_id = native_project(tmp_path, monkeypatch, ctx.api.project)
    ctx.project_plugin = ProjectPlugin(ctx.api)
    mapping = plugin_params.PluginMapping((), "DALI", (), blob_reader="siemens_dali")
    monkeypatch.setattr(plugin_params, "mapping_for_device", lambda _: mapping)
    original = (FIXTURES / "siemens_n141_blob_para.dat").read_bytes()
    project.persist_device_blob(current(project, node_id), "BLOB_PARA", original)

    def section():
        return next(
            s
            for s in project.plugin_sections(current(project, node_id), mapping)
            if s.collection and s.collection.container_tag == "SensorList"
        )

    initial = section()
    async with Client(build_server(ctx)) as client:
        cursor = project.cursor
        added = await _data(
            client,
            "project_add_plugin_item",
            node_id=node_id,
            section=initial.section,
            item_type=initial.collection.item_types[0][0],
            name="Sensor",
        )
        assert added["added"] and project.cursor == cursor + 1
        assert len(section().parameters) == added["count"] > len(initial.parameters)
        added_id = next(
            collection_item_id(p.label)
            for p in section().parameters
            if p not in initial.parameters
        )
        project.undo()
        assert section().parameters == initial.parameters
        project.redo()
        added_blob = project.device_binary_data(current(project, node_id), "BLOB_PARA")
        removed = await _data(
            client,
            "project_remove_plugin_item",
            node_id=node_id,
            section=initial.section,
            item_id=added_id,
        )
        assert removed["removed"] and section().parameters == initial.parameters
        project.undo()
        assert len(section().parameters) == added["count"]
        assert (
            project.device_binary_data(current(project, node_id), "BLOB_PARA")
            == added_blob
        )
        project.redo()
        assert section().parameters == initial.parameters
    project.close()
