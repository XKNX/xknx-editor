"""MDT GUI service, MCP and batch edits share persisted state."""

from pathlib import Path

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server

from .test_server import _build_ctx, _data


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
