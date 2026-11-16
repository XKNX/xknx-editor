"""MDT GUI service, MCP and batch edits share persisted state."""

from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx, _data


async def test_default_library_import_and_read_from_device_share_service(tmp_path):
    from concurrent.futures import Future
    from unittest.mock import Mock

    from editor_gui.plugins.project.tests.test_mdt_default_library import (
        without_attached_app,
    )

    root = next(p for p in Path(__file__).parents if (p / "packages/vendor").is_dir())
    source = root / ".references/mdtdca/~mdtdca.knxproj"
    if not source.exists():
        pytest.skip("Optional MDT project is not installed")
    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    project.import_knxproj(source, tmp_path / "mdt.xknx")
    node = project.devices[2].node_id
    original = project.mdt_symbols(node)
    raw = without_attached_app(project)
    app_path = tmp_path / "symbols.etsapp"
    app_path.write_bytes(raw)
    try:
        async with Client(build_server(ctx)) as client:
            view = await _data(
                client,
                "project_import_mdt_default_library",
                device_address="1.1.3",
                path=str(app_path),
            )
            assert view == original
            assert project.undo()
            result = await _data(
                client,
                "project_batch",
                operations=[
                    {
                        "op": "import_mdt_default_library",
                        "params": {"device_address": "1.1.3", "path": str(app_path)},
                    }
                ],
            )
            assert result["applied"] == 1 and project.mdt_symbols(node) == original
            payload = project.mdt_symbol_payload(node, include_defaults=True)
            future = Future()
            future.set_result(payload)
            ctx.api.connection.read_mdt_symbols = Mock(return_value=future)
            view = await _data(
                client, "project_read_mdt_symbols", device_address="1.1.3"
            )
            assert len(view["symbols"]) == 36 and view["new_library"] is None
            assert project.undo() and project.mdt_symbols(node) == original
    finally:
        project.close()


async def test_recovered_symbols_snapshot_and_project_share_the_gui_service(tmp_path):
    import json

    from editor_gui.plugins.recover.service import RecoverService
    from editor_gui.plugins.recover.tests.test_mdt_recover import _entry, _recovered
    from xknxeditor.vendor.mdt.symbols import recovered_symbol_entries

    root = next(p for p in Path(__file__).parents if (p / "packages/vendor").is_dir())
    source = root / ".references/mdtdca/~mdtdca.knxproj"
    if not source.exists():
        pytest.skip("Optional MDT project is not installed")
    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    project.import_knxproj(source, tmp_path / "mdt.xknx")
    ctx.recover = RecoverService(ctx.api)
    device = project.devices[2]
    profile = project.mdt_dca_profile(device)
    symbols = project.mdt_symbol_payload(device.node_id, include_defaults=True)
    attachments = {profile.app_id: recovered_symbol_entries(symbols, profile)}
    ctx.recover.entries = [
        _entry(project, device, _recovered(device, entries=attachments))
    ]
    ctx.recover.phase = "recovered"
    try:
        async with Client(build_server(ctx)) as client:
            snapshot = await _data(client, "recover_snapshot")
            assert snapshot["devices"][0]["addin_data"][profile.app_id] == {
                k: v.hex() for k, v in attachments[profile.app_id].items()
            }
            assert (
                json.loads(ctx.recover.snapshot_text())["devices"]
                == snapshot["devices"]
            )
            result = await _data(client, "recover_add_to_project")
            assert result["added"] == 1 and result["failed"] == 0
            assert result["entries"][0]["recovered"]["addin_data"] == {
                profile.app_id: 72
            }
            view = await _data(client, "project_mdt_symbols", device_address="1.1.50")
            assert {
                s["slot"]: bytes.fromhex(s["data_hex"]) for s in view["symbols"]
            } == symbols
            assert project.undo()
            assert project.find_device_by_address("1.1.50") is None
    finally:
        project.close()


async def test_symbol_and_layout_edits_share_service_and_undo(tmp_path):
    root = next(p for p in Path(__file__).parents if (p / "packages/vendor").is_dir())
    source = root / ".references/mdtdca/~mdtdca.knxproj"
    if not source.exists():
        pytest.skip("Optional MDT project is not installed")
    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    project.import_knxproj(source, tmp_path / "mdt.xknx")
    node = project.devices[2].node_id
    try:
        async with Client(build_server(ctx)) as client:
            view = await _data(client, "project_mdt_symbols", device_address="1.1.3")
            assert view == project.mdt_symbols(node)
            assert len(view["slot_info"]) == 36
            assert view["slot_info"][0]["editable"] is False
            assert view["slot_info"][2]["role"] == "automatic"
            assert view["slot_info"][2]["name"].endswith("Mode: Automatic")
            assert view["slot_info"][8]["group"] == "functional"
            assert view["slot_info"][9]["group"] == "normal"
            cursor = project.cursor
            result = await _data(
                client,
                "project_set_mdt_symbol",
                node_id=node,
                slot=2,
                grayscale_hex="00" * 4096,
            )
            assert project.cursor == cursor + 1
            assert result == project.mdt_symbols(node)
            project.undo()
            assert project.mdt_symbols(node) == view
            result = await _data(
                client,
                "project_batch",
                operations=[
                    {
                        "op": "set_mdt_symbol",
                        "params": {
                            "device_address": "1.1.3",
                            "slot": 2,
                            "grayscale_hex": "00" * 4096,
                        },
                    }
                ],
            )
            assert result["applied"] == 1
            project.undo()
            assert project.mdt_symbols(node) == view
            layout = await _data(client, "project_mdt_layout", device_address="1.1.4")
            page = layout["pages"][1]
            page["tiles"][0]["color"] = 1
            changed = await _data(
                client,
                "project_set_mdt_layout",
                device_address="1.1.4",
                page_number=page["number"],
                name=page["name"],
                orientation=page["orientation"],
                tiles=page["tiles"],
            )
            assert changed["pages"][1]["tiles"][0]["color"] == 1
            project.undo()
            assert (
                project.mdt_layout(project.devices[3].node_id)["pages"][1]["tiles"][0][
                    "color"
                ]
                == 0
            )
    finally:
        project.close()


async def test_library_tools_batch_and_bus_payloads(tmp_path):
    from concurrent.futures import Future
    from unittest.mock import Mock

    root = next(p for p in Path(__file__).parents if (p / "packages/vendor").is_dir())
    source = root / ".references/mdtdca/~mdtdca.knxproj"
    if not source.exists():
        pytest.skip("Optional MDT project is not installed")
    ctx = _build_ctx(tmp_path)
    project = ctx.api.project
    project.import_knxproj(source, tmp_path / "mdt.xknx")
    node = project.devices[2].node_id
    try:
        async with Client(build_server(ctx)) as client:
            before = project.mdt_symbols(node)
            for newer in (False, True):
                cursor = project.cursor
                result = await _data(
                    client,
                    "project_load_mdt_default_symbols",
                    device_address="1.1.3",
                    new_library=newer,
                )
                assert result == project.mdt_symbols(node)
                assert result["new_library"] == newer and not result["symbols"]
                assert project.cursor == cursor + 1
                project.undo()
                assert project.mdt_symbols(node) == before
            path = tmp_path / "library.zip"
            exported = await _data(
                client,
                "project_export_mdt_symbol_library",
                node_id=node,
                path=str(path),
            )
            assert exported["bytes"] == len(path.read_bytes())
            imported = await _data(
                client,
                "project_import_mdt_symbol_library",
                node_id=node,
                path=str(path),
                invert=True,
            )
            assert len(imported["symbols"]) == 34
            project.undo()
            assert project.mdt_symbols(node) == before
            for op, params in (
                ("load_mdt_default_symbols", {"new_library": False}),
                ("import_mdt_symbol_library", {"path": str(path)}),
            ):
                result = await _data(
                    client,
                    "project_batch",
                    operations=[{"op": op, "params": {"node_id": node, **params}}],
                )
                assert result["applied"] == 1
                project.undo()
                assert project.mdt_symbols(node) == before

            def run(device, symbols, *, writes=False, synchronize=False):
                assert len(symbols) == 36
                future = Future()
                future.set_result(36 if writes else dict.fromkeys(symbols, True))
                return future

            ctx.api.connection.run_mdt_symbols = Mock(side_effect=run)
            for tool, kw in (
                ("project_compare_mdt_symbols", {}),
                ("project_synchronize_mdt_symbols", {}),
                ("project_program_mdt_symbols", {"include_defaults": True}),
            ):
                result = await _data(client, tool, device_address="1.1.3", **kw)
                assert result == 36 or len(result) == 36
            assert (
                ctx.api.connection.run_mdt_symbols.call_args_list[1].kwargs[
                    "synchronize"
                ]
                is True
            )
    finally:
        project.close()
