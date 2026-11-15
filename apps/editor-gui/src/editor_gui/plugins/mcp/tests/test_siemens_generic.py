"""Siemens native configuration is shared by GUI, MCP and batch edits."""

import json
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastmcp import Client

from editor_gui.plugin_providers.siemens_generic import PROVIDER, blob
from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin

from .test_server import _build_ctx, _data

ROOT = next(p for p in Path(__file__).parents if (p / "packages/vendor").is_dir())
ARCHIVE = ROOT / ".references/siemens-generic/~siemens2.knxproj"
pytestmark = pytest.mark.skipif(
    not ARCHIVE.exists(), reason="Optional Siemens project absent"
)


async def test_gui_mcp_batch_and_config_import_share_state(tmp_path):
    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    plugin = ProjectPlugin(ctx.api)
    ctx.project_plugin = plugin
    project.import_knxproj(ARCHIVE, tmp_path / "project.xknx")
    from xknxeditor.prod import parse_master_xml

    with ZipFile(ARCHIVE) as archive:
        ctx.api.connection.master = parse_master_xml(
            archive.read("knx_master.xml"), tmp_path / "cache"
        )
    device = project.devices[0]
    node = device.node_id
    parameter = next(
        p
        for section in PROVIDER.sections(device)
        for p in section.parameters
        if p.writable and len(p.choices) > 1
    )
    alternative = next(v for v, _ in parameter.choices if v != parameter.value)
    before = project.export_plugin_config(device)
    cursor = project.cursor
    plugin.handle_param_change(
        device, parameter.ref_id, alternative, raise_on_error=True
    )
    assert project.cursor == cursor + 1
    after_gui = project.export_plugin_config(project.find_device_by_node_id(node))
    project.undo()
    async with Client(build_server(ctx)) as client:
        capabilities = await _data(client, "project_plugin_capabilities", node_id=node)
        assert capabilities["provider"] == "siemens-generic"
        assert capabilities["configured"] and capabilities["readable"]
        assert capabilities["preflight"]
        preview = await _data(client, "project_preview_plugin", node_id=node)
        assert preview == project.preview_plugin(device, ctx.api.connection.master)
        assert preview["memory"] and preview["resources"]
        assert preview["programming_allowed"] and preview["allowed_scopes"] == ["full"]
        result = await _data(
            client,
            "project_set_plugin_parameter",
            node_id=node,
            ref_id=parameter.ref_id,
            value=alternative,
        )
        assert result["changed"] and result["value"] == alternative
        assert (
            project.export_plugin_config(project.find_device_by_node_id(node))
            == after_gui
        )
        project.undo()
        result = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_plugin_parameter",
                    "params": {
                        "device_address": "1.1.1",
                        "ref_id": parameter.ref_id,
                        "value": alternative,
                    },
                }
            ],
        )
        assert result["applied"] == 1
        assert (
            project.export_plugin_config(project.find_device_by_node_id(node))
            == after_gui
        )
        project.undo()
        assert (
            project.export_plugin_config(project.find_device_by_node_id(node)) == before
        )
        config = json.loads(after_gui)
        result = await _data(
            client,
            "project_import_plugin_config",
            node_id=node,
            config_json=json.dumps(config),
        )
        assert "lost_links" in result
        assert (
            project.export_plugin_config(project.find_device_by_node_id(node))
            == after_gui
        )
        current_blob = blob(project.find_device_by_node_id(node))
        with pytest.raises(Exception, match="Invalid choice"):
            await _data(
                client,
                "project_set_plugin_parameter",
                node_id=node,
                ref_id=parameter.ref_id,
                value="unknown",
            )
        assert blob(project.find_device_by_node_id(node)) == current_blob
    project.close()


async def test_direct_device_memory_comparison_preserves_blob_and_project(
    tmp_path, monkeypatch
):
    from concurrent.futures import Future

    from editor_gui.memory_read import MemoryReadResult
    from editor_gui.plugin_providers.siemens_generic import sources
    from editor_gui.plugins.project.program_queue import ProgramQueue
    from xknxeditor.vendor.siemens_generic import MemoryCheck, parse_settings
    from xknxeditor.vendor.siemens_generic.image import compile_image

    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    plugin = ProjectPlugin(ctx.api)
    ctx.project_plugin = plugin
    project.import_knxproj(ARCHIVE, tmp_path / "read.xknx")
    device = project.devices[0]
    assert plugin.generic_plugin_view(device) is not None
    before = blob(device)
    cursor = project.cursor
    compiled = compile_image(
        sources(device).description,
        parse_settings(before),
        {"DeviceOrderNumber": device.order_number},
    )
    data = bytearray(compiled.data[: compiled.used_length])
    calls = []

    def read(target, request):
        calls.append((target.node_id, request))
        assert (request.address, request.size) == (compiled.base, len(data))
        future = Future()
        future.set_result(MemoryReadResult(request, request.address, bytes(data)))
        return future

    monkeypatch.setattr(ctx.api.connection, "read_memory", read)
    monkeypatch.setattr(
        plugin._memory_preview, "open", lambda *_: pytest.fail("No preview")
    )
    plugin._program_queue = ProgramQueue(
        is_busy=lambda: False, start=lambda item: item.action.start()
    )
    async with Client(build_server(ctx)) as client:
        result = await _data(
            client,
            "connection_read_plugin_memory",
            device_address=device.individual_address,
        )
        assert result["counts"]["match"] > 0
        assert len(calls) == 1
        rows = plugin.generic_plugin_view(device).pages
        matched = next(r for p in rows for r in p.rows if r.memory is MemoryCheck.MATCH)
        data[matched.address - compiled.base] ^= 1
        result = await _data(
            client, "connection_read_plugin_memory", node_id=device.node_id
        )
        assert result["counts"]["mismatch"] > 0
        compared = await _data(
            client,
            "project_generic_plugin_parameters",
            node_id=device.node_id,
            only_mismatches=True,
        )
        assert compared["memory_source"] == "device"
        assert any(
            r["ref_id"] == matched.ref_id
            for p in compared["pages"]
            for r in p["parameters"]
        )
        assert blob(project.find_device_by_node_id(device.node_id)) == before
        assert project.cursor == cursor
        assert plugin.program_queue.history[0].origin == "MCP"
        project.close()
        project.open(tmp_path / "read.xknx")
        fresh = project.devices[0]
        assert plugin.generic_plugin_memory_result(fresh) is None
        assert blob(fresh) == before
    project.close()


def test_comparison_cache_tracks_revision_language_and_device_identity(
    tmp_path, monkeypatch
):
    from editor_gui.plugins.project import plugin as module

    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    plugin = ProjectPlugin(ctx.api)
    project.import_knxproj(ARCHIVE, tmp_path / "cached.xknx")
    device = project.devices[0]
    built = []
    build = plugin._build_generic_plugin_view

    def counted(*args):
        built.append(args[0].node_id)
        return build(*args)

    monkeypatch.setattr(plugin, "_build_generic_plugin_view", counted)
    try:
        initial = plugin.generic_plugin_view(device)
        assert initial is not None
        candidates = plugin.generic_plugin_compare_candidates(device)
        before = len(built)
        for _ in range(4):
            assert plugin._has_plugin_comparison(device)
            assert plugin.generic_plugin_view(device) is initial
            assert plugin.generic_plugin_compare_candidates(device) is candidates
        assert len(built) == before
        addresses = [d.individual_address for d in candidates]
        assert addresses[:2] == ["1.1.2", "1.1.3"]
        assert not any(addresses[2:])
        assert plugin.generic_plugin_view(device, show_unfitted=True) is not initial

        parameter = next(
            p
            for s in PROVIDER.sections(device)
            for p in s.parameters
            if p.writable and len(p.choices) > 1
        )
        alternative = next(v for v, _ in parameter.choices if v != parameter.value)
        project.edit_plugin_parameter(device, parameter.ref_id, alternative)
        current = project.find_device_by_node_id(device.node_id)
        changed = plugin.generic_plugin_view(current)
        assert changed is not initial
        assert plugin.generic_plugin_compare_candidates(current) is not candidates
        project.undo()
        reverted = plugin.generic_plugin_view(
            project.find_device_by_node_id(device.node_id)
        )
        assert reverted is not changed
        project.redo()
        reapplied = plugin.generic_plugin_view(
            project.find_device_by_node_id(device.node_id)
        )
        assert reapplied is not reverted

        language = module.product_language()
        monkeypatch.setattr(
            module,
            "product_language",
            lambda: "de-DE" if language != "de-DE" else "en-US",
        )
        translated = plugin.generic_plugin_view(
            project.find_device_by_node_id(device.node_id)
        )
        assert translated is not reapplied
        ctx.api.catalog.generation += 1
        assert (
            plugin.generic_plugin_view(project.find_device_by_node_id(device.node_id))
            is not translated
        )

        project.close()
        project.open(tmp_path / "cached.xknx")
        fresh = project.devices[0]
        assert plugin.generic_plugin_view(fresh) is not initial
        assert all(
            d is not old
            for d in plugin.generic_plugin_compare_candidates(fresh)
            for old in candidates
        )
    finally:
        project.close()


def test_unrecognised_comparison_is_cached(tmp_path, monkeypatch):
    from editor_gui.device import Device

    ctx = _build_ctx(tmp_path)
    plugin = ProjectPlugin(ctx.api)
    device = Device(node_id=1, name="No application", app=None, individual_address="")
    calls = []
    build = plugin._build_generic_plugin_view
    monkeypatch.setattr(
        plugin,
        "_build_generic_plugin_view",
        lambda *args: calls.append(True) or build(*args),
    )
    assert not plugin._has_plugin_comparison(device)
    assert not plugin._has_plugin_comparison(device)
    assert calls == [True]
