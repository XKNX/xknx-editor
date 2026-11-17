from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.project.plugin import ProjectPlugin
from editor_gui.plugins.project.program_queue import ProgramQueue
from editor_gui.plugins.project.tests.test_mdt_firmware import INFO
from packages.vendor.tests.test_mdt_firmware import metadata_archive

from .test_server import _build_ctx, _data


@pytest.mark.parametrize("queued", [False, True])
async def test_firmware_read_and_catalog_share_gui_service(
    tmp_path, monkeypatch, queued
):
    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    project.new(tmp_path / "test.xknx")
    device = SimpleNamespace(
        node_id=1,
        individual_address="1.1.1",
        name="Test",
        order_number="AKD-0424R.02",
        app=SimpleNamespace(id="M-0083_A-FFFF-01-0000", manufacturer_id="M-0083"),
    )
    monkeypatch.setattr(
        project, "find_device_by_node_id", lambda node: device if node == 1 else None
    )
    monkeypatch.setattr(type(project), "devices", property(lambda _: [device]))
    future = Future()
    future.set_result(
        replace(
            INFO, version="4.10", bsl_info=bytes.fromhex("0100000c83001440") + bytes(20)
        )
    )
    monkeypatch.setattr(
        ctx.api.connection, "read_mdt_firmware", Mock(return_value=future)
    )
    if queued:
        queue = ProgramQueue(
            is_busy=lambda: False, start=lambda item: item.action.start()
        )
        plugin = SimpleNamespace(_api=ctx.api, _program_queue=queue)
        ctx.api.queue_action = lambda *args, **kwargs: ProjectPlugin._queue_bus_action(
            plugin, *args, **kwargs
        )
    path = tmp_path / "catalog.zip"
    path.write_bytes(metadata_archive())
    try:
        async with Client(build_server(ctx)) as client:
            tools = {t.name: t for t in await client.list_tools()}
            assert tools["project_read_mdt_firmware"].annotations.read_only_hint
            assert tools["project_read_mdt_firmware"].annotations.open_world_hint
            assert (
                tools["project_import_mdt_firmware_catalog"].annotations.read_only_hint
                is False
            )
            cursor = project.cursor
            view = await _data(client, "project_read_mdt_firmware", node_id=1)
            assert view == project.mdt_firmware(1)
            assert view["installed_version"] == "4.10"
            assert view["latest_compatible_version"] == "4.10"
            assert view["compatibility_status"] == "up_to_date"
            assert view["bootloader"] == {
                "version_code": 1,
                "crc_type_code": 0,
                "max_telegram_length": 12,
                "encryption_type_code": 0,
            }
            assert (
                view["compatible_releases"][-1]["filename"]
                == "AKD-0424R.02_5328_R410.hex"
            )
            assert "latest_listed_version" not in view
            assert "releases" not in view
            assert {v["hardware_key"] for v in view["catalog_variants"]} == {
                "83000340",
                "83000440",
                "83000540",
                "83001440",
                "83001540",
            }
            assert project.cursor == cursor
            view = await _data(
                client, "project_get_mdt_firmware", device_address="1.1.1"
            )
            assert view == project.mdt_firmware(1)
            assert ctx.api.connection.read_mdt_firmware.call_count == 1
            result = await _data(
                client, "project_import_mdt_firmware_catalog", path=str(path)
            )
            assert result["source"] == "catalog.zip"
            assert project.undo()
            assert project.mdt_firmware(1)["catalog_source"] != "catalog.zip"
    finally:
        project.close()
