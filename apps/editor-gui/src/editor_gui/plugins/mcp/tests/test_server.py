"""End-to-end tests for the embedded MCP server against the live GUI services (headless).

The GUI services are imgui-free, so we build them directly, wire a synchronous ``run_on_ui`` (tools
run inline instead of on a real imgui thread), and drive the tools through FastMCP's in-memory
``Client``. Bus tools are not exercised (no gateway); this covers the project/catalog DATA surface.
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
from fastmcp import Client
from xknx.cemi import CEMIFrame, CEMILData, CEMIMessageCode
from xknx.dpt import DPTBinary
from xknx.telegram import GroupAddress, IndividualAddress, Telegram
from xknx.telegram.apci import DeviceDescriptorRead, GroupValueWrite

from editor_gui.plugins.base import API_VERSION, Logger, PluginAPI
from editor_gui.plugins.catalog.service import CatalogService
from editor_gui.plugins.connection.service import ConnectionService
from editor_gui.plugins.keyring.service import KeyringService
from editor_gui.plugins.logger.service import LogService
from editor_gui.plugins.mcp.context import McpContext
from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.monitor.service import MonitorService
from editor_gui.plugins.project.service import ProjectService

T = TypeVar("T")

_FIXTURE = (
    Path(__file__).parents[7]
    / "packages/prod/tests/fixtures/gira_2gang_button_interface.knxprod"
)


class _StubConnectionPlugin:
    """Stand-in for ConnectionPlugin so connection tools register without a real bus."""

    from editor_gui.plugins.connection.plugin import ConnectionState

    state = ConnectionState.DISCONNECTED
    connected = False
    controller_ip = "127.0.0.1"
    multicast_group = "224.0.23.12"
    is_routing = False


def _build_ctx(tmp_path: Path) -> McpContext:
    log = LogService()
    catalog = CatalogService(tmp_path / "catalog.xknxcatalog")
    project = ProjectService(catalog)
    connection = ConnectionService()
    for svc, name in (
        (catalog, "catalog"),
        (project, "project"),
        (connection, "connection"),
    ):
        if hasattr(svc, "set_logger"):
            svc.set_logger(Logger(log, name))
    monitor = MonitorService(connection)
    monitor.set_logger(Logger(log, "monitor"))
    keyring = KeyringService()
    keyring.set_logger(Logger(log, "keyring"))

    api = PluginAPI(
        api_version=API_VERSION,
        project=project,
        catalog=catalog,
        connection=connection,
        log=log,
    )

    def run_on_ui(fn: Callable[[], T], *, timeout: float = 30.0) -> T:
        return fn()

    return McpContext(
        api=api,
        run_on_ui=run_on_ui,
        connection_plugin=_StubConnectionPlugin(),  # type: ignore[arg-type]
        monitor=monitor,
        keyring=keyring,
        log=Logger(log, "mcp"),
    )


def _build(tmp_path: Path) -> tuple[Any, ProjectService]:
    ctx = _build_ctx(tmp_path)
    return build_server(ctx), ctx.api.project


async def _data(client: Client, tool: str, /, **args: Any) -> Any:
    result = await client.call_tool(tool, args)
    return result.data


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_full_edit_flow(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        # New project.
        assert (await _data(client, "project_new", path=str(tmp_path / "p.xknx")))[
            "open"
        ] is True

        # Import a product into the catalog and find it (list tools return {items, count}).
        added = await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        assert added["added_refs"]
        products = await _data(client, "catalog_list_products")
        assert products["count"] >= 1
        product_ref = products["items"][0]["product_ref_id"]

        # Add the device (returns the created device), then list its com-objects.
        device = await _data(client, "project_add_device", product_ref_id=product_ref)
        node_id = device["node_id"]
        assert isinstance(node_id, int)
        # Device dicts carry the (editable) description, in both the list and the single view.
        listed = await _data(client, "project_list_devices")
        assert all("description" in d for d in listed["items"])
        assert "description" in (
            await _data(client, "project_get_device", node_id=node_id)
        )
        com_objects = (
            await _data(client, "project_list_com_objects", node_id=node_id)
        )["items"]
        assert com_objects
        linkable = next(co for co in com_objects if co["linkable"])
        ref_id, db_id = linkable["ref_id"], linkable["db_id"]

        # Parameters are introspectable (ref_id + allowed values), enabling project_set_parameter.
        params = await _data(client, "project_list_parameters", node_id=node_id)
        assert "count" in params and isinstance(params["items"], list)

        # The nested parameter tree exposes the same parameters under their group headings.
        tree = await _data(client, "project_parameter_tree", node_id=node_id)

        def _params_in(nodes: list[dict[str, Any]]) -> int:
            n = 0
            for nd in nodes:
                if nd.get("kind") == "parameter":
                    n += 1
                n += _params_in(nd.get("children", []))
            return n

        assert _params_in(tree["items"]) == params["count"]

        # Batch: create a GA and link the com-object atomically in one call.
        batch = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "create_group_address",
                    "params": {"address": "1/1/1", "name": "Test"},
                },
            ],
        )
        assert batch["applied"] is True and batch["ok"] == 1
        ga_id = batch["results"][0]["result"]["group_address_id"]

        link = await _data(
            client,
            "project_link_com_object",
            node_id=node_id,
            com_object_ref_id=ref_id,
            group_address_id=ga_id,
        )
        assert isinstance(link["link_id"], int)
        assert link["com_object_db_id"] == db_id

        # The link shows up in the GA's assignments, resolved to its device.
        assignments = (
            await _data(client, "project_get_ga_assignments", group_address_id=ga_id)
        )["items"]
        assert any(a["com_object_db_id"] == db_id for a in assignments)
        assert any(a["node_id"] == node_id for a in assignments)

        # ...and the reverse lookup (com-object -> group addresses) finds the same GA.
        links = (
            await _data(
                client,
                "project_get_com_object_links",
                node_id=node_id,
                com_object_ref_id=ref_id,
            )
        )["items"]
        assert any(link_["group_address_id"] == ga_id for link_ in links)

        # Undo removes the link.
        assert (await _data(client, "project_undo"))["undone"] is True
        assignments = (
            await _data(client, "project_get_ga_assignments", group_address_id=ga_id)
        )["items"]
        assert not any(a["com_object_db_id"] == db_id for a in assignments)

        # status aggregates project + capabilities.
        st = await _data(client, "status")
        assert st["project"]["open"] is True
        assert "FULL" in st["capabilities"]["download_scopes"]

        # Export to a .knxproj archive.
        dest = tmp_path / "out.knxproj"
        assert (await _data(client, "project_export_knxproj", dest=str(dest)))[
            "status"
        ] == "exported"
        assert dest.exists()


def _kinds(nodes: list[dict[str, Any]]) -> set[str]:
    out: set[str] = set()
    for nd in nodes:
        out.add(nd.get("kind"))
        out |= _kinds(nd.get("children", []))
    return out


def _params_in(nodes: list[dict[str, Any]]) -> int:
    n = 0
    for nd in nodes:
        if nd.get("kind") == "parameter":
            n += 1
        n += _params_in(nd.get("children", []))
    return n


def _ids(nodes: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for nd in nodes:
        if nd.get("id"):
            out.append(nd["id"])
        out += _ids(nd.get("children", []))
    return out


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_parameter_tree_outline_and_subtree(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        node_id = (
            await _data(client, "project_add_device", product_ref_id=product_ref)
        )["node_id"]

        # Small device (well under the auto-outline threshold): the plain call returns the full tree.
        full = await _data(client, "project_parameter_tree", node_id=node_id)
        assert full["outline"] is False
        assert "parameter_count" not in full
        assert "parameter" in _kinds(full["items"])

        # outline=True: containers only, each with subtree counts, no parameter/com-object leaves.
        outline = await _data(
            client, "project_parameter_tree", node_id=node_id, outline=True
        )
        assert outline["outline"] is True
        assert _kinds(outline["items"]) <= {"tab", "block"}
        assert all(
            {"parameter_count", "com_object_count"} <= nd.keys()
            for nd in outline["items"]
        )

        # A container id from the outline scopes the read to just that subtree. Scoped reads need
        # tree_token from the same response — without it the server refuses rather than guessing.
        ids = _ids(outline["items"])
        assert ids, "outline should expose at least one container id"
        with pytest.raises(Exception, match="requires tree_token"):
            await _data(
                client,
                "project_parameter_tree",
                node_id=node_id,
                subtree_id=ids[0],
            )
        sub = await _data(
            client,
            "project_parameter_tree",
            node_id=node_id,
            subtree_id=ids[0],
            tree_token=outline["tree_token"],
        )
        assert 0 < _params_in(sub["items"]) <= _params_in(full["items"])

        # An unknown id is a clear error, not an empty result.
        with pytest.raises(Exception, match="no tab/block at id"):
            await _data(
                client,
                "project_parameter_tree",
                node_id=node_id,
                subtree_id="nope",
                tree_token=outline["tree_token"],
            )

        # list_parameters search filters by label.
        allp = await _data(client, "project_list_parameters", node_id=node_id)
        label = next((p["label"] for p in allp["items"] if p.get("label")), "")
        needle = label[: max(3, len(label) // 2)]
        filtered = await _data(
            client, "project_list_parameters", node_id=node_id, search=needle
        )
        assert 0 < filtered["count"] <= allp["count"]
        assert all(
            needle.casefold() in str(p.get("label", "")).casefold()
            for p in filtered["items"]
        )


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_set_parameter_result_and_no_silent_retry(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        node_id = (
            await _data(client, "project_add_device", product_ref_id=product_ref)
        )["node_id"]

        params = (await _data(client, "project_list_parameters", node_id=node_id))[
            "items"
        ]
        enums = [
            p
            for p in params
            if p.get("widget", {}).get("type") == "enum"
            and p.get("access") == "READ_WRITE"
        ]
        if not enums:
            pytest.skip("fixture has no enum parameter to exercise reject/accept")
        a = enums[0]

        # A successful set returns a verified read-back, not just the requested value.
        res = await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=a["ref_id"],
            value=a["value"],
        )
        assert res["ref_id"] == a["ref_id"]
        assert res["requested"] == a["value"]
        assert res["active"] is True
        assert res["value"] == a["value"]

        # A rejected set raises and must NOT take effect — not now, and not via a later retry.
        # (The exact message is type-dependent; assert only that it raises with some reason.)
        with pytest.raises(Exception, match=r".+"):
            await _data(
                client,
                "project_set_parameter",
                node_id=node_id,
                parameter_ref_id=a["ref_id"],
                value="____invalid_choice____",
            )
        after_reject = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            parameter_ref_id=a["ref_id"],
        )
        assert after_reject["value"] == a["value"]

        # A later successful set of ANY parameter does not resurrect the rejected value.
        other = next((p for p in enums[1:]), a)
        await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=other["ref_id"],
            value=other["value"],
        )
        again = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            parameter_ref_id=a["ref_id"],
        )
        assert again["value"] == a["value"]


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_find_potential_com_objects(tmp_path: Path) -> None:
    """find_potential_com_objects lists the application's declared objects (static catalogue) with
    the documented fields."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        node_id = (
            await _data(client, "project_add_device", product_ref_id=product_ref)
        )["node_id"]

        res = await _data(client, "find_potential_com_objects", node_id=node_id)
        assert res["count"] >= 1
        for it in res["items"]:
            assert {
                "ref_id",
                "number",
                "name",
                "function_text",
                "dpt",
                "object_size",
            } <= it.keys()
            assert isinstance(it["dpt"], list)
            assert isinstance(it["number"], int)


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_resolve_parameter_by_label(tmp_path: Path) -> None:
    """project_resolve_parameter maps a human label to a ref_id, errors on an ambiguous or unknown
    label (never guesses), and resolves an enum value_label to its raw value."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        node_id = (
            await _data(client, "project_add_device", product_ref_id=product_ref)
        )["node_id"]

        items = (await _data(client, "project_list_parameters", node_id=node_id))[
            "items"
        ]
        by_label: dict[str, list[dict[str, Any]]] = {}
        for it in items:
            if it.get("label"):
                by_label.setdefault(it["label"], []).append(it)
        unique = next((v[0] for v in by_label.values() if len(v) == 1), None)
        assert unique is not None, (
            "fixture should have at least one uniquely-labelled parameter"
        )

        # A unique label resolves to exactly that parameter.
        res = await _data(
            client, "project_resolve_parameter", node_id=node_id, label=unique["label"]
        )
        assert res["ref_id"] == unique["ref_id"]

        # An unknown label is an error, not a guess.
        with pytest.raises(Exception, match="no active parameter labelled"):
            await _data(
                client,
                "project_resolve_parameter",
                node_id=node_id,
                label="___does_not_exist___",
            )

        # A label repeated across module instances is ambiguous without a scope.
        ambiguous = next((lbl for lbl, v in by_label.items() if len(v) > 1), None)
        if ambiguous is not None:
            with pytest.raises(Exception, match="ambiguous"):
                await _data(
                    client,
                    "project_resolve_parameter",
                    node_id=node_id,
                    label=ambiguous,
                )

        # value_label of an enum resolves to the raw value.
        enum_item = next(
            (
                it
                for it in items
                if it.get("widget", {}).get("type") == "enum"
                and it.get("value_label")
                and len(by_label[it["label"]]) == 1
            ),
            None,
        )
        if enum_item is not None:
            res2 = await _data(
                client,
                "project_resolve_parameter",
                node_id=node_id,
                label=enum_item["label"],
                value_label=enum_item["value_label"],
            )
            assert res2["resolved_value"] == enum_item["value"]


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_set_parameter_requires_editable_target(tmp_path: Path) -> None:
    """A write targets a VISIBLE and user-editable parameter (Access.READ_WRITE). An Access.READ
    parameter is read-only in the GUI; MCP must mirror that and reject the write at both entry
    points (standalone and batch). Codex-identified correctness gap: the old presence-only check
    accepted READ, and the batch path had no check at all."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        node_id = (
            await _data(client, "project_add_device", product_ref_id=product_ref)
        )["node_id"]

        params = (await _data(client, "project_list_parameters", node_id=node_id))[
            "items"
        ]
        read_only = next((p for p in params if p.get("access") == "READ"), None)
        writable = next((p for p in params if p.get("access") == "READ_WRITE"), None)
        assert read_only and writable, (
            "fixture should expose both READ and READ_WRITE parameters"
        )

        # READ is rejected by the standalone setter with a clear message.
        with pytest.raises(Exception, match="not user-editable"):
            await _data(
                client,
                "project_set_parameter",
                node_id=node_id,
                parameter_ref_id=read_only["ref_id"],
                value=str(read_only.get("value") or "0"),
            )
        # The read-only value is unchanged.
        after = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            parameter_ref_id=read_only["ref_id"],
        )
        assert after["value"] == read_only["value"]

        # The same ref via the batch path is also rejected now, with full rollback.
        bad = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_parameter",
                    "params": {
                        "node_id": node_id,
                        "parameter_ref_id": read_only["ref_id"],
                        "value": str(read_only.get("value") or "0"),
                    },
                }
            ],
        )
        assert bad["failed"] == 1
        assert "not user-editable" in bad["results"][0]["error"]

        # A READ_WRITE parameter still works (regression check).
        ok = await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=writable["ref_id"],
            value=str(writable["value"]),
        )
        assert ok["active"] is True

        # An unknown ref is rejected with the "not visible" message.
        with pytest.raises(Exception, match="not visible on device"):
            await _data(
                client,
                "project_set_parameter",
                node_id=node_id,
                parameter_ref_id="M-0008_A-7072-21-5CC3-O000A_P-99999_R-99999",
                value="0",
            )


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_tools_accept_device_address_and_group_address_strings(
    tmp_path: Path,
) -> None:
    """Device-scoped tools accept device_address (e.g. "1.1.1") instead of node_id, and
    project_link_com_object + the batch GA ops accept group_address (e.g. "0/0/1") instead of
    group_address_id. Saves two round-trips (list_devices + list_group_addresses) per call. Exactly
    one of each pair must be given; neither / both / unknown raises clearly."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        node_id = (
            await _data(client, "project_add_device", product_ref_id=product_ref)
        )["node_id"]
        addr = (await _data(client, "project_get_device", node_id=node_id))[
            "individual_address"
        ]
        assert addr, "a fresh device should have a configured individual address"

        # 1) project_get_device, project_list_com_objects, project_list_parameters accept the str.
        by_addr = await _data(client, "project_get_device", device_address=addr)
        assert by_addr["node_id"] == node_id
        by_addr_cos = await _data(
            client, "project_list_com_objects", device_address=addr
        )
        by_id_cos = await _data(client, "project_list_com_objects", node_id=node_id)
        assert by_addr_cos["count"] == by_id_cos["count"]
        by_addr_params = await _data(
            client, "project_list_parameters", device_address=addr
        )
        assert by_addr_params["count"] > 0

        # 2) Neither / both forms raise.
        with pytest.raises(Exception, match="exactly one"):
            await _data(client, "project_get_device")
        with pytest.raises(Exception, match="exactly one"):
            await _data(
                client, "project_get_device", node_id=node_id, device_address=addr
            )
        with pytest.raises(Exception, match="no device with individual_address"):
            await _data(client, "project_get_device", device_address="99.99.99")

        # 3) project_link_com_object accepts BOTH device_address and group_address.
        co = next(co for co in by_id_cos["items"] if co["linkable"])
        ga_addr = "0/0/9"
        g_id = (await _data(client, "project_create_group_address", address=ga_addr))[
            "group_address_id"
        ]
        linked = await _data(
            client,
            "project_link_com_object",
            device_address=addr,
            com_object_ref_id=co["ref_id"],
            group_address=ga_addr,
            is_sending=True,
        )
        assert linked["already_linked"] is False
        assert linked["group_address_id"] == g_id
        # Idempotency via the string form.
        again = await _data(
            client,
            "project_link_com_object",
            device_address=addr,
            com_object_ref_id=co["ref_id"],
            group_address=ga_addr,
        )
        assert again["already_linked"] is True

        # 4) batch set_parameter + link_com_object accept the string forms too.
        enum_param = next(
            (
                p
                for p in by_addr_params["items"]
                if p.get("widget", {}).get("type") == "enum"
                and p.get("value") is not None
            ),
            None,
        )
        if enum_param is not None:
            res = await _data(
                client,
                "project_batch",
                operations=[
                    {
                        "op": "set_parameter",
                        "params": {
                            "device_address": addr,
                            "parameter_ref_id": enum_param["ref_id"],
                            "value": enum_param["value"],
                        },
                    }
                ],
            )
            assert res["ok"] == 1 and res["results"][0]["result"]["node_id"] == node_id

        # 5) Unknown GA by string raises on link.
        with pytest.raises(Exception, match="no group address"):
            await _data(
                client,
                "project_link_com_object",
                node_id=node_id,
                com_object_ref_id=co["ref_id"],
                group_address="99/9/999",
            )


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_tree_token_detects_stale_scope(tmp_path: Path) -> None:
    """A scoped resolve/write refuses a tree_token that no longer matches the device structure, so
    a path that silently points to a different container after a structural change cannot retarget
    the write. Codex's finding: index paths are positional and survive a reshape with a different
    meaning; the token guards against that."""
    # A parameter whose value change reshapes the tree in Gira: toggling it activates/deactivates
    # 29 downstream params in the same module instance, re-indexing the siblings.
    parent = "M-0008_A-7072-21-5CC3-O000A_MD-1_M-200_MI-1_P-149_R-142"
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        node_id = (
            await _data(client, "project_add_device", product_ref_id=product_ref)
        )["node_id"]

        outline = await _data(
            client, "project_parameter_tree", node_id=node_id, outline=True
        )
        token_a = outline["tree_token"]
        ids = _ids(outline["items"])
        assert ids
        sid = ids[-1]

        # Reshape the tree: changing the parent's value adds / removes active sub-parameters.
        await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=parent,
            value="3",
        )
        outline_b = await _data(
            client, "project_parameter_tree", node_id=node_id, outline=True
        )
        token_b = outline_b["tree_token"]
        assert token_a != token_b, "a reshape must change the tree_token"

        # The stale token on the SAME subtree_id is rejected by every scoped entry.
        with pytest.raises(Exception, match="stale tree_token"):
            await _data(
                client,
                "project_parameter_tree",
                node_id=node_id,
                subtree_id=sid,
                tree_token=token_a,
            )
        with pytest.raises(Exception, match="stale tree_token"):
            await _data(
                client,
                "project_resolve_parameter",
                node_id=node_id,
                label="whatever",
                subtree_id=sid,
                tree_token=token_a,
            )
        bad = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_parameter",
                    "params": {
                        "node_id": node_id,
                        "label": "whatever",
                        "subtree_id": sid,
                        "tree_token": token_a,
                        "value": "0",
                    },
                }
            ],
        )
        assert bad["failed"] == 1
        assert "stale tree_token" in bad["results"][0]["error"]

        # A scoped call WITHOUT a token is also refused — the server never falls back to a bare path.
        with pytest.raises(Exception, match="requires tree_token"):
            await _data(
                client,
                "project_resolve_parameter",
                node_id=node_id,
                label="whatever",
                subtree_id=sid,
            )


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_tree_token_survives_ordinary_edit(tmp_path: Path) -> None:
    """Ordinary (non-structural) value changes keep the mapping intact, so the tree_token stays
    valid — otherwise every edit would force a re-read."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        node_id = (
            await _data(client, "project_add_device", product_ref_id=product_ref)
        )["node_id"]

        outline = await _data(
            client, "project_parameter_tree", node_id=node_id, outline=True
        )
        token = outline["tree_token"]
        # Pick a parameter whose set does NOT reshape the tree (its own ref_id stays active).
        params = (await _data(client, "project_list_parameters", node_id=node_id))[
            "items"
        ]
        stable = next(
            (
                p
                for p in params
                if p.get("widget", {}).get("type") == "number"
                and p.get("value") is not None
            ),
            params[0],
        )
        await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=stable["ref_id"],
            value=stable["value"],
        )
        again = await _data(
            client, "project_parameter_tree", node_id=node_id, outline=True
        )
        assert again["tree_token"] == token


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_tree_token_is_per_device(tmp_path: Path) -> None:
    """A tree_token from one device does not pass the check on another device, even for the same
    path, because the device node_id goes into the fingerprint."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        a = (await _data(client, "project_add_device", product_ref_id=product_ref))[
            "node_id"
        ]
        b = (await _data(client, "project_add_device", product_ref_id=product_ref))[
            "node_id"
        ]
        o_a = await _data(client, "project_parameter_tree", node_id=a, outline=True)
        with pytest.raises(Exception, match="stale tree_token"):
            await _data(
                client,
                "project_parameter_tree",
                node_id=b,
                subtree_id=_ids(o_a["items"])[0],
                tree_token=o_a["tree_token"],
            )


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_batch_activates_child_in_order(tmp_path: Path) -> None:
    """A batch whose earlier op activates a parameter a later op then sets: the batch applies ops
    sequentially, so the child is active in time and an ordered parent->child batch succeeds, while
    the reverse order fails and rolls back. Pins the dependency issue to edit ORDER (and the child's
    ref only materialising after the switch), not to missing deferred validation."""
    parent = "M-0008_A-7072-21-5CC3-O000A_MD-1_M-200_MI-1_P-135_R-136"
    child = "M-0008_A-7072-21-5CC3-O000A_MD-1_M-200_MI-1_UP-168_R-236"
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        node_id = (
            await _data(client, "project_add_device", product_ref_id=product_ref)
        )["node_id"]

        def _set(ref: str, value: str) -> Any:
            return _data(
                client,
                "project_set_parameter",
                node_id=node_id,
                parameter_ref_id=ref,
                value=value,
            )

        # Learn a valid child value by briefly switching the parent on, then revert.
        await _set(parent, "1")
        cval = (
            await _data(
                client, "project_get_parameter", node_id=node_id, parameter_ref_id=child
            )
        )["value"]
        await _set(parent, "0")

        # With the parent off the child is inactive: a direct set is rejected (the single tool
        # reports it as an unknown parameter since it is absent from the UI).
        with pytest.raises(Exception, match=r"not visible on device|not active"):
            await _set(child, cval)

        def _batch(order: list[tuple[str, str]], **kwargs: Any) -> Any:
            return _data(
                client,
                "project_batch",
                operations=[
                    {
                        "op": "set_parameter",
                        "params": {
                            "node_id": node_id,
                            "parameter_ref_id": ref,
                            "value": val,
                        },
                    }
                    for ref, val in order
                ],
                **kwargs,
            )

        # Ordered parent->child: the switch op activates the child before its op runs.
        ok = await _batch([(parent, "1"), (child, cval)])
        assert ok["ok"] == 2 and ok["failed"] == 0 and not ok["rolled_back"]

        # Reverse order child->parent: with default deferred=True, multi-pass retries child after parent activates it.
        await _set(parent, "0")
        deferred_ok = await _batch([(child, cval), (parent, "1")])
        assert (
            deferred_ok["ok"] == 2
            and deferred_ok["failed"] == 0
            and not deferred_ok["rolled_back"]
        )

        # With deferred=False (strict single-pass), reverse order child->parent fails and rolls back.
        await _set(parent, "0")
        bad = await _batch([(child, cval), (parent, "1")], deferred=False)
        assert bad["failed"] >= 1 and bad["rolled_back"] is True
        assert bad["results"][0]["status"] == "error"


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_batch_resolves_child_by_label_at_apply_time(tmp_path: Path) -> None:
    """A batch op addressed by label is resolved AT ITS TURN: an earlier op switches a parent on,
    then a later op names the child by its (now unique, active) label and resolves it — which a plain
    ref cannot express because the child's ref is not active up front. This closes the parent->child
    dependency the tester hit."""
    parent = "M-0008_A-7072-21-5CC3-O000A_MD-1_M-200_MI-1_P-135_R-136"
    child = "M-0008_A-7072-21-5CC3-O000A_MD-1_M-200_MI-1_UP-168_R-236"
    child_label = "At the beginning of the disabling function"
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        node_id = (
            await _data(client, "project_add_device", product_ref_id=product_ref)
        )["node_id"]

        # Parent off: the child is inactive, so its label resolves to nothing.
        with pytest.raises(Exception, match="no active parameter labelled"):
            await _data(
                client, "project_resolve_parameter", node_id=node_id, label=child_label
            )

        # Batch: switch the parent on by ref, then set the child BY LABEL (resolved at its turn).
        res = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_parameter",
                    "params": {
                        "node_id": node_id,
                        "parameter_ref_id": parent,
                        "value": "1",
                    },
                },
                {
                    "op": "set_parameter",
                    "params": {
                        "node_id": node_id,
                        "label": child_label,
                        "value": "0",
                    },
                },
            ],
        )
        assert res["ok"] == 2 and res["failed"] == 0 and not res["rolled_back"]
        assert res["results"][1]["result"]["ref_id"] == child


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_link_feedback_dedup_dpt_and_sending(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        node_id = (
            await _data(client, "project_add_device", product_ref_id=product_ref)
        )["node_id"]
        cos = (await _data(client, "project_list_com_objects", node_id=node_id))[
            "items"
        ]
        co = next(c for c in cos if c["linkable"])
        co_dpt = co.get("dpt")

        g1 = (await _data(client, "project_create_group_address"))["group_address_id"]

        # First link: created, not a duplicate.
        r1 = await _data(
            client,
            "project_link_com_object",
            node_id=node_id,
            com_object_ref_id=co["ref_id"],
            group_address_id=g1,
            is_sending=True,
        )
        assert r1["already_linked"] is False

        # Re-linking the same pair is idempotent: same link, no duplicate row.
        r2 = await _data(
            client,
            "project_link_com_object",
            node_id=node_id,
            com_object_ref_id=co["ref_id"],
            group_address_id=g1,
            is_sending=True,
        )
        assert r2["already_linked"] is True
        assert r2["link_id"] == r1["link_id"]
        links = await _data(
            client,
            "project_get_com_object_links",
            node_id=node_id,
            com_object_ref_id=co["ref_id"],
        )
        assert links["count"] == 1

        # A second GA with a deliberately different DPT, linked sending: the result surfaces the
        # DPT mismatch and that the object already sends to g1.
        g2 = (await _data(client, "project_create_group_address"))["group_address_id"]
        other_dpt = "5.001" if (co_dpt or "").startswith("1") else "1.001"
        await _data(
            client, "project_set_group_address_dpt", group_address_id=g2, dpt=other_dpt
        )
        r3 = await _data(
            client,
            "project_link_com_object",
            node_id=node_id,
            com_object_ref_id=co["ref_id"],
            group_address_id=g2,
            is_sending=True,
        )
        assert r3["already_linked"] is False
        if co_dpt:
            assert r3["dpt_mismatch"]["group_address"] == other_dpt
        assert g1 in r3.get("other_sending_group_address_ids", [])


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_project_tools_require_open_project(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        status = await _data(client, "project_status")
        assert status["open"] is False
        with pytest.raises(Exception, match="no project is open"):
            await client.call_tool("project_add_device", {"product_ref_id": "x"})


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_delete_product(tmp_path: Path) -> None:
    """Deleting a product empties the catalog and leaves the device using it unloaded."""
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        await _data(client, "project_add_device", product_ref_id=ref)
        assert len(project.devices) == 1

        result = await _data(client, "catalog_delete_product", product_ref_id=ref)
        assert result["product_ref_id"] == ref
        assert result["affected_devices"] == 1
        assert len(result["removed_programs"]) == 1
        assert len(result["removed_files"]) == 1
        assert (await _data(client, "catalog_list_products"))["count"] == 0
        assert project.devices == []
        assert len(project.unloaded_devices) == 1

        with pytest.raises(Exception, match="not in the local catalog"):
            await client.call_tool("catalog_delete_product", {"product_ref_id": ref})


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_additional_address_tools(tmp_path: Path) -> None:
    """Additional individual addresses are assigned, set and read through the project tools."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        device = await _data(client, "project_add_device", product_ref_id=ref)
        node_id = device["node_id"]
        line = device["individual_address"].rsplit(".", 1)[0]
        info = await _data(client, "project_get_device", node_id=node_id)
        assert info["additional_addresses"] == []

        assigned = await _data(
            client, "project_assign_additional_addresses", node_id=node_id, count=2
        )
        assert [e["address"] for e in assigned["additional_addresses"]] == [
            f"{line}.255",
            f"{line}.254",
        ]
        changed = await _data(
            client,
            "project_set_additional_address",
            node_id=node_id,
            index=2,
            address=f"{line}.200",
            name="Tunnel 2",
        )
        assert changed["additional_addresses"][1] == {
            "index": 2,
            "address": f"{line}.200",
            "name": "Tunnel 2",
            "description": "",
        }
        cleared = await _data(
            client,
            "project_set_additional_address",
            node_id=node_id,
            index=1,
            address=None,
        )
        assert cleared["additional_addresses"][0]["address"] is None
        info = await _data(client, "project_get_device", node_id=node_id)
        assert [e["address"] for e in info["additional_addresses"]] == [
            None,
            f"{line}.200",
        ]
        with pytest.raises(Exception, match="already used in this line"):
            await client.call_tool(
                "project_set_additional_address",
                {
                    "node_id": node_id,
                    "index": 1,
                    "address": device["individual_address"],
                },
            )
        with pytest.raises(Exception, match="device number from 1 to 255"):
            await client.call_tool(
                "project_set_additional_address",
                {"node_id": node_id, "index": 1, "address": "0"},
            )
        with pytest.raises(Exception, match="not provide enough addresses"):
            await client.call_tool(
                "project_assign_additional_addresses",
                {"node_id": node_id, "count": 255},
            )
        # Undo of the clear brings the first address back.
        await _data(client, "project_undo")
        info = await _data(client, "project_get_device", node_id=node_id)
        assert info["additional_addresses"][0]["address"] == f"{line}.255"


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_import_knxprod_bytes(tmp_path: Path) -> None:
    """A .knxprod imports inline as base64 (remote client), then is readable via catalog tools."""
    import base64 as _b64

    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        payload = _b64.b64encode(_FIXTURE.read_bytes()).decode()
        added = await _data(
            client,
            "catalog_import_knxprod_bytes",
            filename="dev.knxprod",
            data_base64=payload,
        )
        assert added["added_refs"]
        assert (await _data(client, "catalog_list_products"))["count"] >= 1

        # a non-ZIP payload is rejected before any import is attempted
        with pytest.raises(Exception, match=r"not a \.knxprod archive"):
            await client.call_tool(
                "catalog_import_knxprod_bytes",
                {
                    "filename": "x.knxprod",
                    "data_base64": _b64.b64encode(b"not a zip").decode(),
                },
            )


async def test_catalog_docs_builds_manufacturer_links(tmp_path: Path) -> None:
    """catalog_docs returns a site-scoped manufacturer search plus a web search (no network/project)."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        result = await _data(
            client,
            "catalog_docs",
            query="dimming curve",
            manufacturer="MDT technologies",
        )
        assert result["manufacturer"] == "MDT technologies"
        assert result["note"]  # guidance about large PDFs / preferring structured tools
        kinds = {r["kind"] for r in result["references"]}
        assert {"knx_database", "manufacturer_site", "web"} <= kinds
        knx = next(r for r in result["references"] if r["kind"] == "knx_database")
        assert "knx.org" in knx["url"]
        site = next(r for r in result["references"] if r["kind"] == "manufacturer_site")
        assert "mdt.de" in site["url"]


def test_manufacturer_domain_resolution() -> None:
    """Domain lookup is substring-based, so ordering and qualified keys matter (regression guard)."""
    from editor_gui.plugins.mcp.tools.catalog import _domain_for

    # more specific key wins where one catalog name contains another
    assert _domain_for("ABB AG - BUSCH-JAEGER") == "busch-jaeger.de"
    assert _domain_for("ABB AG - STOTZ-KONTAKT") == "abb.com"
    # qualified keys do not leak into unrelated names
    assert _domain_for("Insta GmbH") == "insta.de"
    assert _domain_for("Instell") is None
    assert _domain_for("MDT technologies") == "mdt.de"
    assert _domain_for(None) is None
    assert _domain_for("Totally Unknown Maker") is None


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_catalog_docs_includes_resolved_document_url(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """With a product_ref_id, catalog_docs returns a best-effort resolved document_url AND the
    search references as a fallback (the resolver is mocked -- no network)."""
    import editor_gui.plugins.mcp.tools.catalog as catalog_tools

    monkeypatch.setattr(
        catalog_tools,
        "_resolve_manual_url",
        lambda *a, **k: "https://www.mdt.de/download/MDT_THB.pdf",
    )
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        docs = await _data(
            client, "catalog_docs", query="channel A", product_ref_id=ref
        )
        assert docs["document_url"] == "https://www.mdt.de/download/MDT_THB.pdf"
        assert docs["references"]  # links are still returned as a fallback


def test_extra_doc_hosts_allowed() -> None:
    """Manufacturer alternate / distributor doc hosts are fetchable; unknown hosts are not."""
    import editor_gui.plugins.mcp.tools.catalog as catalog_tools

    for url in (
        "https://www.basalte.world/x.pdf",
        "https://hager.ch/x.pdf",
        "https://partner.gira.com/x.pdf",  # subdomain of gira.com
        "https://siblik.com/x.pdf",
        "https://www.futurasmus-knxgroup.es/x.pdf",
    ):
        assert catalog_tools._host_allowed(url), url
    assert not catalog_tools._host_allowed("https://evil.example.com/x.pdf")


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_resolved_document_host_becomes_fetchable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A host the resolver returned via catalog_docs becomes fetchable, without being hardcoded."""
    import editor_gui.plugins.mcp.tools.catalog as catalog_tools

    catalog_tools._RESOLVED_DOC_HOSTS.discard("random-reseller.example")
    monkeypatch.setattr(
        catalog_tools,
        "_resolve_manual_url",
        lambda *a, **k: "https://random-reseller.example/manual.pdf",
    )
    assert not catalog_tools._host_allowed("https://random-reseller.example/manual.pdf")
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        await _data(client, "catalog_docs", query="x", product_ref_id=ref)
    assert catalog_tools._host_allowed("https://random-reseller.example/manual.pdf")
    catalog_tools._RESOLVED_DOC_HOSTS.discard("random-reseller.example")


def _two_page_pdf() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


async def test_docs_fetch_reads_pages_without_dumping(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """catalog_docs_fetch returns page count + outline, a single page, and rejects bad input."""
    import editor_gui.plugins.mcp.tools.catalog as catalog_tools

    monkeypatch.setattr(
        catalog_tools, "_download_doc", lambda url, refresh=False: _two_page_pdf()
    )
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        meta = await _data(client, "catalog_docs_fetch", url="https://knx.org/x.pdf")
        assert meta["total_pages"] == 2
        assert "outline" in meta

        one = await _data(
            client, "catalog_docs_fetch", url="https://knx.org/x.pdf", page=1
        )
        assert one["page"] == 1 and "text" in one

        with pytest.raises(Exception, match="out of range"):
            await client.call_tool(
                "catalog_docs_fetch", {"url": "https://knx.org/x.pdf", "page": 9}
            )


def test_download_pdf_is_cached(tmp_path: Path, monkeypatch: Any) -> None:
    """A PDF URL is downloaded once, then served from the on-disk cache (and re-read offline)."""
    import editor_gui.plugins.mcp.tools.catalog as catalog_tools

    monkeypatch.setattr(catalog_tools, "_docs_cache_dir", lambda: tmp_path)
    catalog_tools._MEM_CACHE.clear()
    calls = {"n": 0}

    def _fake_get(url: str) -> bytes:
        calls["n"] += 1
        return _two_page_pdf()

    monkeypatch.setattr(catalog_tools, "_http_get", _fake_get)
    first = catalog_tools._download_doc("https://knx.org/a.pdf")
    catalog_tools._MEM_CACHE.clear()  # force the on-disk path, not the process cache
    second = catalog_tools._download_doc("https://knx.org/a.pdf")
    assert first == second and calls["n"] == 1
    # it was persisted and indexed under a recognisable filename
    assert catalog_tools._read_index()
    catalog_tools._MEM_CACHE.clear()


def test_normalize_github_blob_url() -> None:
    """A github.com blob URL is rewritten to the raw file so the document (not HTML) is fetched."""
    from editor_gui.plugins.mcp.tools.catalog import _normalize_doc_url

    assert (
        _normalize_doc_url(
            "https://github.com/OpenKNX/OFM-LedModule/blob/v1/doc/Applikationsbeschreibung-LedDimmer.md"
        )
        == "https://raw.githubusercontent.com/OpenKNX/OFM-LedModule/v1/doc/Applikationsbeschreibung-LedDimmer.md"
    )
    # non-blob / non-github URLs pass through unchanged
    assert _normalize_doc_url("https://www.mdt.de/x.pdf") == "https://www.mdt.de/x.pdf"


async def test_docs_fetch_reads_markdown(tmp_path: Path, monkeypatch: Any) -> None:
    """A markdown doc is read as text (heading outline + capped text), and find returns line hits."""
    import editor_gui.plugins.mcp.tools.catalog as catalog_tools

    md = b"# Title\n\nintro line\n\n## Section A\n\nthe magic value is 42\n\n## Section B\n"
    monkeypatch.setattr(catalog_tools, "_download_doc", lambda url, refresh=False: md)
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        meta = await _data(
            client,
            "catalog_docs_fetch",
            url="https://raw.githubusercontent.com/OpenKNX/x/v1/doc/a.md",
        )
        assert meta["content_type"] == "text"
        titles = [h["title"] for h in meta["outline"]]
        assert titles == ["Title", "Section A", "Section B"]
        assert "magic value" in meta["text"]

        hit = await _data(
            client,
            "catalog_docs_fetch",
            url="https://raw.githubusercontent.com/OpenKNX/x/v1/doc/a.md",
            find="magic",
        )
        assert hit["match_count"] == 1
        assert hit["matches"][0]["line"] == 7


async def test_docs_fetch_rejects_disallowed_host(tmp_path: Path) -> None:
    """The fetch is restricted to documentation hosts (no open SSRF)."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="host not allowed"):
            await client.call_tool(
                "catalog_docs_fetch", {"url": "https://evil.example.com/x.pdf"}
            )


async def test_mutation_rejected_while_project_locked(tmp_path: Path) -> None:
    """A project mutation must reject with 'project busy' while a background worker holds io_lock."""
    ctx = _build_ctx(tmp_path)
    mcp = build_server(ctx)
    lock = ctx.api.catalog.io_lock
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))

        holding, release = threading.Event(), threading.Event()

        def _hold() -> None:  # stand-in for a background import/open worker
            lock.acquire()
            holding.set()
            release.wait(5.0)
            lock.release()

        worker = threading.Thread(target=_hold)
        worker.start()
        try:
            assert holding.wait(5.0)
            with pytest.raises(Exception, match="project busy"):
                await client.call_tool(
                    "project_create_group_address", {"address": "1/1/1"}
                )
        finally:
            release.set()
            worker.join(5.0)


def _cemi(code: CEMIMessageCode, destination: Any, payload: Any, src: str) -> bytes:
    telegram = Telegram(destination_address=destination, payload=payload)
    data = CEMILData.init_from_telegram(telegram, src_addr=IndividualAddress(src))
    return CEMIFrame(code=code, data=data).to_knx()


async def test_network_tools_read_the_monitor_logs(tmp_path: Path) -> None:
    """The network_* tools drive and read the Group Monitor and Bus Activity together."""
    ctx = _build_ctx(tmp_path)
    mcp = build_server(ctx)
    monitor = ctx.monitor
    group = _cemi(
        CEMIMessageCode.L_DATA_IND,
        GroupAddress("1/2/3"),
        GroupValueWrite(DPTBinary(1)),
        "1.1.1",
    )
    request = _cemi(
        CEMIMessageCode.L_DATA_REQ,
        IndividualAddress("1.1.5"),
        DeviceDescriptorRead(),
        "1.1.250",
    )
    other = _cemi(
        CEMIMessageCode.L_DATA_IND,
        IndividualAddress("1.1.7"),
        DeviceDescriptorRead(),
        "1.1.5",
    )
    monitor.on_raw_cemi(group)
    monitor.on_sent_cemi(request)
    monitor.on_raw_cemi(other)
    async with Client(mcp) as client:
        state = await _data(client, "network_state")
        assert state["state"] == "capturing"
        assert state["recording"] == {"group_monitor": True, "bus_activity": True}
        assert state["telegrams"] == 3
        assert state["cemi_records"] == 3

        telegrams = await _data(client, "network_telegrams", limit=0)
        assert telegrams["count"] == 3
        items = telegrams["items"]
        assert [i["monitor"] for i in items] == [
            "group_monitor",
            "bus_activity",
            "bus_activity",
        ]
        assert all(i["source_type"] == "connection" for i in items)
        assert "GroupValueWrite" in items[0]["telegram"]
        assert 'direction="Outgoing"' in items[1]["telegram"]
        assert 'direction="Incoming"' in items[2]["telegram"]
        assert items[0]["timestamp"] <= items[2]["timestamp"]
        newest = await _data(client, "network_telegrams", limit=1)
        assert newest["count"] == 1
        assert "1.1.7" in newest["items"][0]["telegram"]

        cemi = await _data(client, "network_cemi_records")
        assert cemi["count"] == 3
        first = cemi["items"][0]
        assert first["msg_code"] == "L_DATA_IND"
        assert first["src_addr"] == "1.1.1"
        assert first["dst_addr"] == "1/2/3"
        assert isinstance(first["hops"], int)
        assert first["raw"] == group.hex(" ")
        assert [i["msg_code"] for i in cemi["items"]] == [
            "L_DATA_IND",
            "L_DATA_REQ",
            "L_DATA_IND",
        ]
        assert cemi["items"][2]["dst_addr"] == "1.1.7"

        assert (await _data(client, "status"))["capture"]["telegrams"] == 3

        assert (await _data(client, "network_stop"))["status"] == "stopped"
        state = await _data(client, "network_state")
        assert state["state"] == "stopped"
        monitor.on_raw_cemi(other)
        assert state["cemi_records"] == 3
        assert (await _data(client, "network_state"))["cemi_records"] == 3

        assert (await _data(client, "network_start"))["status"] == "capturing"
        assert (await _data(client, "network_state"))["recording"] == {
            "group_monitor": True,
            "bus_activity": True,
        }
        assert (await _data(client, "network_clear"))["status"] == "cleared"
        cleared = await _data(client, "network_state")
        assert cleared["cemi_records"] == 0
        assert cleared["state"] == "capturing"


_KNXPROJ = (
    Path(__file__).parents[7]
    / "packages/proj/tests/fixtures/xknx_test_project_no_password.knxproj"
)


@pytest.mark.skipif(not _KNXPROJ.exists(), reason="knxproj fixture missing")
async def test_project_compact(tmp_path: Path) -> None:
    """project_compact reports unused product data on a dry run and removes it otherwise."""
    import sqlite3
    import zipfile

    from xknxeditor.proj import import_knxproj

    path = tmp_path / "p.xknx"
    import_knxproj(_KNXPROJ, path)
    with sqlite3.connect(path) as db:
        (blob,) = db.execute("SELECT imported_product_members FROM projects").fetchone()
        buffer = io.BytesIO()
        with (
            zipfile.ZipFile(io.BytesIO(blob)) as src,
            zipfile.ZipFile(buffer, "w") as out,
        ):
            for name in src.namelist():
                out.writestr(name, src.read(name))
            out.writestr(
                "M-0003/Hardware.xml",
                '<KNX xmlns="http://knx.org/xml/project/20"><ManufacturerData>'
                '<Manufacturer RefId="M-0003"><Hardware><Hardware Id="M-0003_H-1">'
                '<Products><Product Id="M-0003_H-1_P-1" /></Products>'
                "</Hardware></Hardware></Manufacturer></ManufacturerData></KNX>",
            )
            out.writestr("M-0003/M-0003_A-0001.xml", "<KNX />")
        db.execute(
            "UPDATE projects SET imported_product_members = ?", (buffer.getvalue(),)
        )
    db.close()

    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_open", path=str(path))
        dry = await _data(client, "project_compact")
        assert dry["status"] == "analysed"
        assert dry["removed"]["manufacturers"] == ["M-0003"]
        assert dry["removed"]["application_programs"] == ["M-0003_A-0001"]

        done = await _data(client, "project_compact", dry_run=False)
        assert done["status"] == "compacted"
        assert done["removed"]["manufacturers"] == ["M-0003"]
        assert done["file_size_after"] == path.stat().st_size

        again = await _data(client, "project_compact")
        assert again["nothing_unused"] is True


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_programming_tools_record_the_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import Future

    ctx = _build_ctx(tmp_path)
    connection = ctx.api.connection
    recorded: list[tuple[Any, ...]] = []

    def done(result: Any = None) -> Future[Any]:
        future: Future[Any] = Future()
        future.set_result(result)
        return future

    monkeypatch.setattr(ConnectionService, "xknx", property(lambda self: object()))
    monkeypatch.setattr(
        connection, "program_device", lambda *a, **k: done(b"\x00\xfa\x01\x02\x03\x04")
    )
    monkeypatch.setattr(
        connection, "assign_individual_address_for_device", lambda device: done()
    )
    monkeypatch.setattr(
        ctx.api.project,
        "record_download",
        lambda *args: recorded.append(args),
    )
    async with Client(build_server(ctx)) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        node_id = (
            await _data(
                client, "project_add_device", product_ref_id=product["product_ref_id"]
            )
        )["node_id"]
        await _data(
            client, "project_set_individual_address", node_id=node_id, address="1.1.5"
        )
        await _data(client, "connection_program_device", node_id=node_id)
        await _data(client, "connection_assign_individual_address", node_id=node_id)
    assert recorded == [
        (node_id, "full", b"\x00\xfa\x01\x02\x03\x04", None),
        (node_id, "ia"),
    ]


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_programming_runs_in_the_operations_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from concurrent.futures import Future

    from editor_gui.plugins.project.program_queue import (
        ORIGIN_MCP,
        OperationState,
        QueueItem,
    )

    ctx = _build_ctx(tmp_path)
    connection = ctx.api.connection
    monkeypatch.setattr(ConnectionService, "xknx", property(lambda self: object()))
    direct: list[str] = []

    def program_device(*args: object, **kwargs: object) -> None:
        direct.append("program")

    def assign_individual_address(device: object) -> None:
        direct.append("ia")

    monkeypatch.setattr(connection, "program_device", program_device)
    monkeypatch.setattr(
        connection, "assign_individual_address_for_device", assign_individual_address
    )
    queued: list[QueueItem] = []
    outcome: dict[str, Any] = {"state": OperationState.FINISHED, "message": None}

    def queue_program(
        node_id: int, scope: Any, *, origin: str, **options: Any
    ) -> QueueItem:
        item = QueueItem(
            node_id=node_id,
            address="1.1.5",
            name="D",
            scope=scope,
            origin=origin,
            ended=Future(),
            **options,
        )
        queued.append(item)
        if outcome["state"] is not None:
            item.state = outcome["state"]
            item.message = outcome["message"]
            assert item.ended is not None
            item.ended.set_result(item)
        return item

    canceled: list[QueueItem] = []
    ctx.api.queue_program = queue_program
    ctx.api.cancel_operation = canceled.append
    async with Client(build_server(ctx)) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        node_id = (
            await _data(
                client, "project_add_device", product_ref_id=product["product_ref_id"]
            )
        )["node_id"]
        await _data(
            client, "project_set_individual_address", node_id=node_id, address="1.1.5"
        )
        programmed = await _data(
            client, "connection_program_device", node_id=node_id, scope="PARAMETERS"
        )
        assert programmed == {"status": "programmed", "node_id": node_id}
        assigned = await _data(
            client, "connection_assign_individual_address", node_id=node_id
        )
        assert assigned["status"] == "assigned"
        outcome.update(state=OperationState.CANCELED, message="Cancelled by user.")
        with pytest.raises(Exception, match="Cancelled by user"):
            await client.call_tool("connection_program_device", {"node_id": node_id})
        outcome.update(state=None)  # never ends: the tool gives up and cancels it
        with pytest.raises(Exception, match="did not finish"):
            await client.call_tool(
                "connection_program_device", {"node_id": node_id, "timeout": 0.2}
            )
    assert direct == []
    assert [(i.scope.name if i.scope else None, i.origin) for i in queued] == [
        ("PARAMETERS", ORIGIN_MCP),
        ("INDIVIDUAL_ADDRESS", ORIGIN_MCP),
        ("FULL", ORIGIN_MCP),
        ("FULL", ORIGIN_MCP),
    ]
    assert canceled == [queued[-1]]


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_parameters_of_a_plugin_dialog_device_cannot_be_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A device whose parameters the manufacturer's plugin dialog sets refuses parameter
    changes, alone and in a batch."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        products = await _data(client, "catalog_list_products")
        product_ref = products["items"][0]["product_ref_id"]
        node_id = (
            await _data(client, "project_add_device", product_ref_id=product_ref)
        )["node_id"]
        params = (await _data(client, "project_list_parameters", node_id=node_id))[
            "items"
        ]
        assert params
        ref_id = params[0]["ref_id"]

        for module in ("project", "batch"):
            monkeypatch.setattr(
                f"editor_gui.plugins.mcp.tools.{module}.parameters_in_plugin",
                lambda _device: True,
            )
        result = await client.call_tool(
            "project_set_parameter",
            {"node_id": node_id, "parameter_ref_id": ref_id, "value": "0"},
            raise_on_error=False,
        )
        assert result.is_error
        assert "plugin dialog" in str(result.content[0])
        batch = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_parameter",
                    "params": {
                        "node_id": node_id,
                        "parameter_ref_id": ref_id,
                        "value": "0",
                    },
                }
            ],
        )
        assert batch["applied"] is False
        assert "plugin dialog" in str(batch)


async def test_rename_group_ranges_functions_and_device_descriptions(
    tmp_path: Path,
) -> None:
    """Group ranges, functions and device descriptions are editable over MCP (issue #132)."""
    mcp, project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))

        # Group ranges: listed with their ids, renamed directly and via project_batch.
        main = project.create_group_range(None, "Lihct")
        assert main is not None
        middle = project.create_group_range(main, "Dimmewert Status")
        assert middle is not None
        ranges = (await _data(client, "project_list_group_ranges"))["items"]
        by_id = {r["id"]: r for r in ranges}
        assert by_id[main]["depth"] == 0
        assert by_id[middle]["parent_id"] == main
        renamed = await _data(
            client, "project_rename_group_range", group_range_id=main, name="Licht"
        )
        assert renamed["name"] == "Licht"
        batch = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "rename_group_range",
                    "params": {"group_range_id": middle, "name": "Dimmwert Status"},
                }
            ],
        )
        assert batch["ok"] == 1
        dimm = (await _data(client, "project_list_group_ranges", name_contains="dimm"))[
            "items"
        ]
        assert [(r["id"], r["name"]) for r in dimm] == [(middle, "Dimmwert Status")]

        # An unnamed range keeps a fixed item shape; filtering and renaming to "" still work.
        unnamed = project.create_group_range(main, "")
        assert unnamed is not None
        ranges = (await _data(client, "project_list_group_ranges"))["items"]
        item = next(r for r in ranges if r["id"] == unnamed)
        assert (item["name"], item["description"]) == ("", "")
        assert (
            await _data(client, "project_list_group_ranges", name_contains="licht")
        )["count"] == 1
        cleared = await _data(
            client, "project_rename_group_range", group_range_id=middle, name=""
        )
        assert cleared["name"] == ""
        await _data(client, "project_undo")

        # Names are stripped like in the GUI.
        padded = await _data(
            client,
            "project_rename_group_range",
            group_range_id=main,
            name="  Licht EG ",
        )
        assert padded["name"] == "Licht EG"

        # Functions: the id comes from project_spaces.
        room = (
            await _data(
                client, "project_create_space", space_type="Room", name="Wohnen"
            )
        )["space_id"]
        function = project.create_function(room, "FT-1", "Wohenn Deckenlicht")
        assert function is not None
        result = await _data(
            client,
            "project_rename_function",
            function_id=function,
            name=" Wohnen Deckenlicht ",
        )
        assert result == {"function_id": function, "name": "Wohnen Deckenlicht"}

        def _functions(nodes: list[dict[str, Any]]) -> list[str]:
            out: list[str] = []
            for node in nodes:
                out += [f["name"] for f in node.get("functions", [])]
                out += _functions(node.get("children", []))
            return out

        spaces = (await _data(client, "project_spaces"))["items"]
        assert "Wohnen Deckenlicht" in _functions(spaces)
        with pytest.raises(Exception, match="no function"):
            await client.call_tool(
                "project_rename_function", {"function_id": 999999, "name": "x"}
            )
        await _data(client, "project_undo")
        spaces = (await _data(client, "project_spaces"))["items"]
        assert "Wohenn Deckenlicht" in _functions(spaces)


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_rename_device_sets_name_and_description(tmp_path: Path) -> None:
    """project_rename_device and the rename_device batch op also set the description."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        node_id = (
            await _data(
                client, "project_add_device", product_ref_id=product["product_ref_id"]
            )
        )["node_id"]

        result = await _data(
            client,
            "project_rename_device",
            node_id=node_id,
            description="OGKind 1 (Terasse)",
        )
        assert result["description"] == "OGKind 1 (Terasse)"
        result = await _data(
            client,
            "project_rename_device",
            node_id=node_id,
            name="Taster",
            description="OG Kind 1 (Terrasse)",
        )
        assert (result["name"], result["description"]) == (
            "Taster",
            "OG Kind 1 (Terrasse)",
        )
        batch = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "rename_device",
                    "params": {"node_id": node_id, "description": "Flur"},
                }
            ],
        )
        assert batch["ok"] == 1
        device = await _data(client, "project_get_device", node_id=node_id)
        assert (device["name"], device["description"]) == ("Taster", "Flur")
        with pytest.raises(Exception, match="name, description or both"):
            await client.call_tool("project_rename_device", {"node_id": node_id})


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_direct_addressing_by_device_and_group_address(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        products = await _data(client, "catalog_list_products")
        product_ref = products["items"][0]["product_ref_id"]
        dev_res = await _data(client, "project_add_device", product_ref_id=product_ref)
        node_id = dev_res["node_id"]

        # Newly created device gets 1.1.1 by default in new project
        dev_initial = await _data(client, "project_get_device", device_address="1.1.1")
        assert dev_initial["node_id"] == node_id
        assert dev_initial["individual_address"] == "1.1.1"

        # Change individual address
        await _data(
            client,
            "project_set_individual_address",
            node_id=node_id,
            address="1.1.10",
        )

        # Test project_get_device by device_address
        dev = await _data(client, "project_get_device", device_address="1.1.10")
        assert dev["node_id"] == node_id
        assert dev["individual_address"] == "1.1.10"

        # Test project_list_parameters by device_address
        params = await _data(client, "project_list_parameters", device_address="1.1.10")
        assert params["count"] > 0

        # Test project_list_com_objects by device_address
        cos = await _data(client, "project_list_com_objects", device_address="1.1.10")
        assert cos["count"] > 0
        linkable_co = next(c for c in cos["items"] if c["linkable"])

        # Create group address
        ga_res = await _data(
            client, "project_create_group_address", address="1/1/1", name="Light 1"
        )
        ga_id = ga_res["group_address_id"]

        # Test project_get_group_address by string
        ga = await _data(client, "project_get_group_address", group_address="1/1/1")
        assert ga["id"] == ga_id
        assert ga["address"] == "1/1/1"

        # Link com object using device_address and group_address strings
        link_res = await _data(
            client,
            "project_link_com_object",
            device_address="1.1.10",
            group_address="1/1/1",
            com_object_ref_id=linkable_co["ref_id"],
        )
        assert link_res["device_address"] == "1.1.10"
        assert link_res["group_address"] == "1/1/1"
        assert not link_res["already_linked"]

        # Check assignments using group_address
        assignments = await _data(
            client, "project_get_ga_assignments", group_address="1/1/1"
        )
        assert assignments["count"] == 1
        assert assignments["items"][0]["individual_address"] == "1.1.10"

        # Unlink using device_address, com_object_ref_id, and group_address
        unlinked = await _data(
            client,
            "project_unlink_com_object",
            device_address="1.1.10",
            group_address="1/1/1",
            com_object_ref_id=linkable_co["ref_id"],
        )
        assert unlinked["status"] == "unlinked"

        # Test batch operations using device_address and group_address
        batch_res = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "rename_device",
                    "params": {"device_address": "1.1.10", "name": "Kitchen Sensor"},
                },
                {
                    "op": "rename_group_address",
                    "params": {
                        "group_address": "1/1/1",
                        "name": "Kitchen Ceiling Light",
                    },
                },
                {
                    "op": "link_com_object",
                    "params": {
                        "device_address": "1.1.10",
                        "group_address": "1/1/1",
                        "com_object_ref_id": linkable_co["ref_id"],
                    },
                },
            ],
        )
        assert batch_res["ok"] == 3
        assert batch_res["failed"] == 0


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_list_parameters_filters_and_compact(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        products = await _data(client, "catalog_list_products")
        product_ref = products["items"][0]["product_ref_id"]
        dev_res = await _data(client, "project_add_device", product_ref_id=product_ref)
        node_id = dev_res["node_id"]

        # 1. Compact projection
        compact_res = await _data(
            client, "project_list_parameters", node_id=node_id, compact=True
        )
        assert compact_res["count"] > 0
        first_compact = compact_res["items"][0]
        assert "ref_id" in first_compact
        assert "label" in first_compact
        assert "value" in first_compact
        assert "widget" not in first_compact
        assert "default_value" not in first_compact
        assert "access" not in first_compact

        # 2. Query filter
        full_res = await _data(client, "project_list_parameters", node_id=node_id)
        label = next((p["label"] for p in full_res["items"] if p.get("label")), "")
        assert label
        needle = label[: max(3, len(label) // 2)]
        query_res = await _data(
            client, "project_list_parameters", node_id=node_id, query=needle
        )
        assert 0 < query_res["count"] <= full_res["count"]
        assert all(
            needle.casefold() in str(p.get("label", "")).casefold()
            for p in query_res["items"]
        )

        # 3. Modified only filter
        before_mod = await _data(
            client, "project_list_parameters", node_id=node_id, modified_only=True
        )
        assert before_mod["count"] == 0

        # Change one parameter value using a valid alternative choice
        enums = [
            p for p in full_res["items"] if p.get("widget", {}).get("type") == "enum"
        ]
        assert enums
        target_param = enums[0]
        ref = target_param["ref_id"]
        detail = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            parameter_ref_id=ref,
        )
        choices = detail.get("widget", {}).get("choices", [])
        alt_choice = next(
            str(c["value"])
            for c in choices
            if str(c["value"]) != str(target_param["value"])
        )

        await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=ref,
            value=alt_choice,
        )

        after_mod = await _data(
            client, "project_list_parameters", node_id=node_id, modified_only=True
        )
        assert after_mod["count"] >= 1
        assert any(p["ref_id"] == ref for p in after_mod["items"])


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_semantic_addressing_with_channel_and_direct_set(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        products = await _data(client, "catalog_list_products")
        product_ref = products["items"][0]["product_ref_id"]
        dev_res = await _data(client, "project_add_device", product_ref_id=product_ref)
        node_id = dev_res["node_id"]

        params_res = await _data(client, "project_list_parameters", node_id=node_id)
        # Find an enum parameter with label
        target = None
        for p in params_res["items"]:
            if p.get("label") and p.get("widget", {}).get("type") == "enum":
                target = p
                break
        assert target is not None
        target_label = target["label"]
        channel = target.get("channel")
        detail = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            parameter_ref_id=target["ref_id"],
        )
        choices = detail.get("widget", {}).get("choices", [])
        assert len(choices) > 1
        alt_choice = next(c for c in choices if str(c["value"]) != str(target["value"]))
        alt_val = str(alt_choice["value"])
        alt_text = alt_choice["label"]

        # 1. Resolve with label (+ channel if present)
        resolved = await _data(
            client,
            "project_resolve_parameter",
            node_id=node_id,
            label=target_label,
            channel=channel,
            value_label=alt_text,
        )
        assert resolved["ref_id"] == target["ref_id"]
        assert resolved["resolved_value"] == alt_val

        # 2. Direct set using label and value_label (no parameter_ref_id)
        set_res = await _data(
            client,
            "project_set_parameter",
            device_address="1.1.1",
            label=target_label,
            channel=channel,
            value_label=alt_text,
        )
        assert set_res["ref_id"] == target["ref_id"]
        assert str(set_res["value"]) == alt_val
        assert "activated_com_objects" in set_res
        assert isinstance(set_res["activated_com_objects"], list)
        assert "unlocked_parameters" in set_res
        assert isinstance(set_res["unlocked_parameters"], list)

        # 3. Direct set via project_batch using label and value
        # Pick another choice or switch back to original
        orig_val = str(target["value"])
        batch_res = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "set_parameter",
                    "params": {
                        "device_address": "1.1.1",
                        "label": target_label,
                        "channel": channel,
                        "value": orig_val,
                    },
                }
            ],
        )
        assert batch_res["applied"] is True
        assert batch_res["ok"] == 1
        res0 = batch_res["results"][0]["result"]
        assert str(res0["value"]) == orig_val
        assert "activated_com_objects" in res0
        assert "unlocked_parameters" in res0


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_set_parameter_feedback_activated_cos_and_unlocked_params(
    tmp_path: Path,
) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        products = await _data(client, "catalog_list_products")
        product_ref = products["items"][0]["product_ref_id"]
        dev_res = await _data(client, "project_add_device", product_ref_id=product_ref)
        node_id = dev_res["node_id"]

        params_res = await _data(client, "project_list_parameters", node_id=node_id)
        # Iterate over enum parameters until we find one that unlocks parameters or com-objects
        activated = None
        for p in params_res["items"]:
            if p.get("widget", {}).get("type") == "enum":
                detail = await _data(
                    client,
                    "project_get_parameter",
                    node_id=node_id,
                    parameter_ref_id=p["ref_id"],
                )
                choices = detail.get("widget", {}).get("choices", [])
                for c in choices:
                    if str(c["value"]) != str(p["value"]):
                        res = await _data(
                            client,
                            "project_set_parameter",
                            node_id=node_id,
                            parameter_ref_id=p["ref_id"],
                            value=str(c["value"]),
                        )
                        if res["activated_com_objects"] or res["unlocked_parameters"]:
                            activated = res
                            break
                if activated is not None:
                    break

        assert activated is not None, (
            "Expected at least one parameter change to activate KOs or parameters"
        )
        if activated["activated_com_objects"]:
            first_co = activated["activated_com_objects"][0]
            assert "ref_id" in first_co
            assert "number" in first_co
            assert "name" in first_co
            assert "dpt" in first_co
            assert "linkable" in first_co
        if activated["unlocked_parameters"]:
            first_up = activated["unlocked_parameters"][0]
            assert "ref_id" in first_up
            assert "label" in first_up
            assert "value" in first_up
            assert "widget" in first_up


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_project_diff_session(tmp_path: Path) -> None:
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))

        # Clean project initially
        clean_diff = await _data(client, "project_diff")
        assert clean_diff["mode"] == "session"
        assert clean_diff["is_dirty"] is False
        assert clean_diff["summary"]["parameters_changed"] == 0
        assert clean_diff["summary"]["group_addresses_added"] == 0
        assert clean_diff["summary"]["links_added"] == 0

        # Import product and add device
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        products = await _data(client, "catalog_list_products")
        product_ref = products["items"][0]["product_ref_id"]
        dev_res = await _data(client, "project_add_device", product_ref_id=product_ref)
        node_id = dev_res["node_id"]

        # Create GA
        ga_res = await _data(
            client, "project_create_group_address", address="1/2/3", name="Test GA"
        )
        assert ga_res["address"] == "1/2/3"

        # Change a parameter
        params_res = await _data(client, "project_list_parameters", node_id=node_id)
        target = next(
            p for p in params_res["items"] if p.get("widget", {}).get("type") == "enum"
        )
        pref_id = target["ref_id"]
        detail = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            parameter_ref_id=pref_id,
        )
        choices = detail.get("widget", {}).get("choices", [])
        alt_choice = next(c for c in choices if str(c["value"]) != str(target["value"]))
        new_val = str(alt_choice["value"])
        await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=pref_id,
            value=new_val,
        )

        # Link com-object
        cos_res = await _data(client, "project_list_com_objects", node_id=node_id)
        linkable_cos = [co for co in cos_res["items"] if co.get("linkable")]
        assert linkable_cos, "Expected at least one linkable com-object"
        co_ref = linkable_cos[0]["ref_id"]
        await _data(
            client,
            "project_link_com_object",
            node_id=node_id,
            com_object_ref_id=co_ref,
            group_address="1/2/3",
        )

        # Diff should show all edits
        diff = await _data(client, "project_diff")
        assert diff["mode"] == "session"
        assert diff["is_dirty"] is True
        assert diff["summary"]["group_addresses_added"] == 1
        assert diff["summary"]["links_added"] == 1
        assert diff["summary"]["parameters_changed"] >= 1
        assert diff["differences"]["group_addresses"]["added"][0]["address"] == "1/2/3"
        assert diff["differences"]["links"]["added"][0]["group_address"] == "1/2/3"
        assert any(p["ref_id"] == pref_id for p in diff["differences"]["parameters"])

        # Undo the link, parameter and GA
        await _data(client, "project_undo")  # undo link
        await _data(client, "project_undo")  # undo param
        await _data(client, "project_undo")  # undo GA
        undone_diff = await _data(client, "project_diff")
        assert undone_diff["summary"]["group_addresses_added"] == 0
        assert undone_diff["summary"]["links_added"] == 0


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_project_diff_file(tmp_path: Path) -> None:
    import shutil

    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        baseline_path = tmp_path / "baseline.xknx"
        current_path = tmp_path / "current.xknx"
        await _data(client, "project_new", path=str(current_path))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        products = await _data(client, "catalog_list_products")
        product_ref = products["items"][0]["product_ref_id"]
        dev_res = await _data(client, "project_add_device", product_ref_id=product_ref)
        node_id = dev_res["node_id"]

        # Make a copy as baseline
        shutil.copyfile(current_path, baseline_path)

        # Diff before new changes against baseline should have 0 diff
        diff0 = await _data(client, "project_diff", compare_against=str(baseline_path))
        assert diff0["mode"] == "file"
        assert diff0["is_dirty"] is False
        assert diff0["summary"]["parameters_changed"] == 0
        assert diff0["summary"]["group_addresses_added"] == 0

        # Now add a GA and modify a parameter in current
        await _data(
            client,
            "project_create_group_address",
            address="2/3/4",
            name="Baseline Diff GA",
        )
        params_res = await _data(client, "project_list_parameters", node_id=node_id)
        target = next(
            p for p in params_res["items"] if p.get("widget", {}).get("type") == "enum"
        )
        pref_id = target["ref_id"]
        detail = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            parameter_ref_id=pref_id,
        )
        choices = detail.get("widget", {}).get("choices", [])
        alt_choice = next(c for c in choices if str(c["value"]) != str(target["value"]))
        new_val = str(alt_choice["value"])
        await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=pref_id,
            value=new_val,
        )

        # Diff against baseline
        diff1 = await _data(client, "project_diff", compare_against=str(baseline_path))
        assert diff1["mode"] == "file"
        assert diff1["is_dirty"] is True
        assert diff1["summary"]["group_addresses_added"] == 1
        assert diff1["differences"]["group_addresses"]["added"][0]["address"] == "2/3/4"
        assert diff1["summary"]["parameters_changed"] >= 1


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_semantic_path_addressing(tmp_path: Path) -> None:
    """Test resolving, reading and writing parameters using hierarchical paths."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        prod_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        dev = await _data(client, "project_add_device", product_ref_id=prod_ref)
        node_id = dev["node_id"]

        # Resolve parameter by hierarchy path
        resolved = await _data(
            client,
            "project_resolve_parameter",
            node_id=node_id,
            path="Channel 1 / Channel function",
        )
        assert resolved["ref_id"]
        assert resolved["label"] == "Channel function"
        assert "Channel 1" in resolved.get("path", "")

        # Read parameter by hierarchy path
        param = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            path="Channel 1 / Channel function",
        )
        assert param["ref_id"] == resolved["ref_id"]
        assert param["label"] == "Channel function"

        # Set parameter by hierarchy path
        set_res = await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            path="Channel 1 / Channel function",
            value="1",
        )
        assert set_res["active"] is True
        assert set_res["ref_id"] == resolved["ref_id"]


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_ineffective_link_detection(tmp_path: Path) -> None:
    """Test detection of ineffective com-object links when linked while inactive/hidden."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        prod_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        dev = await _data(client, "project_add_device", product_ref_id=prod_ref)
        node_id = dev["node_id"]

        ga = await _data(
            client, "project_create_group_address", address="1/1/10", name="Test GA"
        )
        ga_id = ga["group_address_id"]

        cos = (await _data(client, "project_list_com_objects", node_id=node_id))[
            "items"
        ]
        co = cos[0]

        link = await _data(
            client,
            "project_link_com_object",
            node_id=node_id,
            com_object_ref_id=co["ref_id"],
            group_address_id=ga_id,
        )
        assert "is_effective" in link
        # If effective, no warning; if ineffective, warning present
        if not link["is_effective"]:
            assert "ineffective_warning" in link

        co_info = await _data(
            client,
            "project_get_com_object",
            node_id=node_id,
            com_object_ref_id=co["ref_id"],
        )
        assert "is_effective" in co_info


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_project_find_free_slots(tmp_path: Path) -> None:
    """Test project_find_free_slots inspecting channels and functions."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        prod_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        dev = await _data(client, "project_add_device", product_ref_id=prod_ref)
        node_id = dev["node_id"]

        slots_info = await _data(client, "project_find_free_slots", node_id=node_id)
        assert slots_info["node_id"] == node_id
        assert "total_slots" in slots_info
        assert "slots" in slots_info
        assert len(slots_info["slots"]) == slots_info["total_slots"]
        assert all(
            "status" in s and "selector_ref_id" in s for s in slots_info["slots"]
        )

        # Test filtering by slot_type
        filtered = await _data(
            client, "project_find_free_slots", node_id=node_id, slot_type="Channel"
        )
        assert "slots" in filtered


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_project_setup_parameters(tmp_path: Path) -> None:
    """Test project_setup_parameters and batch setup_parameters."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        prod_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        dev = await _data(client, "project_add_device", product_ref_id=prod_ref)
        node_id = dev["node_id"]

        # Setup parameters with dry_run
        plan = await _data(
            client,
            "project_setup_parameters",
            node_id=node_id,
            parameters=[{"path": "Channel 1 / Channel function", "value": "1"}],
            dry_run=True,
        )
        assert plan["status"] == "planned"
        assert plan["applied"] is False
        assert len(plan["planned_edits"]) >= 1

        # Apply setup_parameters
        res = await _data(
            client,
            "project_setup_parameters",
            node_id=node_id,
            parameters=[{"path": "Channel 1 / Channel function", "value": "1"}],
        )
        assert res["applied"] is True
        assert len(res["planned_edits"]) >= 1

        # Test via batch
        batch_res = await _data(
            client,
            "project_batch",
            operations=[
                {
                    "op": "setup_parameters",
                    "params": {
                        "node_id": node_id,
                        "parameters": [
                            {"path": "Channel 2 / Channel function", "value": "1"}
                        ],
                    },
                }
            ],
        )
        assert batch_res["applied"] is True
        assert batch_res["ok"] == 1


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_set_parameter_value_label_with_ref_id(tmp_path: Path) -> None:
    """Test setting a parameter using parameter_ref_id and value_label without label/path."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        prod_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        dev = await _data(client, "project_add_device", product_ref_id=prod_ref)
        node_id = dev["node_id"]

        params = (await _data(client, "project_list_parameters", node_id=node_id))[
            "items"
        ]
        enum_param = next(
            p
            for p in params
            if p.get("widget", {}).get("type") == "enum"
            and p.get("access") == "READ_WRITE"
        )
        pref_id = enum_param["ref_id"]

        param_details = await _data(
            client,
            "project_get_parameter",
            node_id=node_id,
            parameter_ref_id=pref_id,
        )
        choices = param_details["widget"]["choices"]
        choice_label = choices[0]["label"]

        # Call project_set_parameter with parameter_ref_id and value_label
        res = await _data(
            client,
            "project_set_parameter",
            node_id=node_id,
            parameter_ref_id=pref_id,
            value_label=choice_label,
        )
        assert res["active"] is True
        assert res["value"] == str(choices[0]["value"])

        # Call via batch set_parameter with parameter_ref_id and value_label
        if len(choices) > 1:
            alt_label = choices[1]["label"]
            batch_res = await _data(
                client,
                "project_batch",
                operations=[
                    {
                        "op": "set_parameter",
                        "params": {
                            "node_id": node_id,
                            "parameter_ref_id": pref_id,
                            "value_label": alt_label,
                        },
                    }
                ],
            )
            assert batch_res["applied"] is True
            assert batch_res["ok"] == 1


def _normalize_tool(tool: Any) -> dict[str, Any]:
    if hasattr(tool, "model_dump"):
        data = tool.model_dump(by_alias=True)
    elif isinstance(tool, dict):
        data = tool
    else:
        data = {
            "name": getattr(tool, "name", ""),
            "description": getattr(tool, "description", None),
            "inputSchema": getattr(
                tool, "inputSchema", getattr(tool, "parameters", {})
            ),
            "annotations": getattr(tool, "annotations", None),
        }
    ann = data.get("annotations")
    if hasattr(ann, "model_dump"):
        ann = ann.model_dump(by_alias=True)
    elif not isinstance(ann, dict):
        ann = None

    schema = data.get("inputSchema")
    if schema is None:
        schema = data.get("parameters", {})

    return {
        "name": data.get("name"),
        "description": data.get("description"),
        "inputSchema": schema,
        "annotations": ann,
    }


async def test_tools_golden_snapshot(tmp_path: Path) -> None:
    """Compare in-process list_tools() against tests/golden/tools.json.

    The golden file currently captures the 108 core MCP tools (without knowledge tools).
    When the Knowledge project merges its tools, update this snapshot using UPDATE_GOLDEN=1.

    Usage to update snapshot:
        UPDATE_GOLDEN=1 uv run pytest apps/editor-gui/src/editor_gui/plugins/mcp/tests/test_server.py -k test_tools_golden_snapshot
    """
    golden_path = Path(__file__).parent / "golden" / "tools.json"
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        tools = await client.list_tools()
        normalized = sorted(
            [_normalize_tool(t) for t in tools],
            key=lambda x: str(x["name"]),
        )
        current_data = {t["name"]: t for t in normalized}

        if os.environ.get("UPDATE_GOLDEN") == "1":
            golden_path.parent.mkdir(parents=True, exist_ok=True)
            golden_path.write_text(
                json.dumps(normalized, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            return

        assert golden_path.exists(), (
            f"Golden file {golden_path} does not exist. Run with UPDATE_GOLDEN=1 to create it."
        )

        golden_list = json.loads(golden_path.read_text(encoding="utf-8"))
        golden_data = {t["name"]: t for t in golden_list}

        current_names = set(current_data.keys())
        golden_names = set(golden_data.keys())

        added = sorted(current_names - golden_names)
        removed = sorted(golden_names - current_names)
        changed: list[str] = []
        for name in sorted(current_names & golden_names):
            if current_data[name] != golden_data[name]:
                changed.append(name)

        if added or removed or changed:
            msg_parts = ["MCP tools snapshot mismatch against tests/golden/tools.json:"]
            if added:
                msg_parts.append(f"  Added tools ({len(added)}): {', '.join(added)}")
            if removed:
                msg_parts.append(
                    f"  Removed tools ({len(removed)}): {', '.join(removed)}"
                )
            if changed:
                msg_parts.append(
                    f"  Modified tools ({len(changed)}): {', '.join(changed)}"
                )
            msg_parts.append("\nTo update golden snapshot, run with UPDATE_GOLDEN=1.")
            pytest.fail("\n".join(msg_parts))


def test_satisfying_value_normalization() -> None:
    """_satisfying_value_for_condition normalizes relational operators with whitespace."""
    from editor_gui.plugins.mcp.tools.project import _satisfying_value_for_condition

    # Operator with whitespace
    assert _satisfying_value_for_condition("!= 0") == "1"
    assert _satisfying_value_for_condition(">= 2") == "2"
    assert _satisfying_value_for_condition("<= 5") == "5"
    assert _satisfying_value_for_condition("> 3") == "4"
    assert _satisfying_value_for_condition("< 3") == "2"
    assert _satisfying_value_for_condition("== 4") == "4"
    assert _satisfying_value_for_condition("= 4") == "4"
    assert _satisfying_value_for_condition("!= 1") == "0"
    # Compact operators without whitespace
    assert _satisfying_value_for_condition("!=0") == "1"
    assert _satisfying_value_for_condition(">=2") == "2"
    assert _satisfying_value_for_condition("<=5") == "5"
    assert _satisfying_value_for_condition(">0") == "1"


def test_scan_device_slots_secondary_parameters_exclusion() -> None:
    """_scan_device_slots recognizes primary slot selectors like 'Betriebsart Zeitschaltuhr'
    while excluding secondary parameters ('Entprellzeit', 'Verzögerung bei Betriebsart Treppenlicht',
    'Timeout model', 'Dimmmode time')."""
    from unittest.mock import MagicMock

    from editor_gui.plugins.mcp.tools.project import _scan_device_slots
    from xknxeditor.namespaces.intermediate import ParameterTypeTypeRestrictionBase
    from xknxeditor.prod.parser_v2.ui import EnumChoice, EnumWidget, UiParameter, UiTab

    widget = EnumWidget(
        choices=(
            EnumChoice(id="c0", value=0, label="Aus"),
            EnumChoice(id="c1", value=1, label="Aktiviert"),
            EnumChoice(id="c2", value=2, label="Erweitert"),
        ),
        base=ParameterTypeTypeRestrictionBase.VALUE,
    )

    # 1. Primary selector that contains 'zeit' as substring in compound word 'Zeitschaltuhr'
    primary_p = UiParameter(
        ref_id="P_PRIMARY",
        label="Betriebsart Zeitschaltuhr",
        value="1",
        widget=widget,
    )
    tab1 = UiTab(children=(primary_p,), text="Kanal 1")
    dev1 = MagicMock()
    dev1.node_id = 1
    dev1.individual_address = "1.1.1"
    dev1.get_ui.return_value = [tab1]

    res1 = _scan_device_slots(dev1)
    assert len(res1["slots"]) == 1
    assert res1["slots"][0]["selector_label"] == "Betriebsart Zeitschaltuhr"

    # 2. Pure secondary parameters must NOT be identified as slot selectors
    secondary_labels = [
        "Entprellzeit",
        "Verzögerung bei Betriebsart Treppenlicht",
        "Timeout model",
        "Dimmmode time",
    ]
    for idx, lbl in enumerate(secondary_labels):
        p = UiParameter(
            ref_id=f"P_SEC_{idx}",
            label=lbl,
            value="1",
            widget=widget,
        )
        tab_sec = UiTab(children=(p,), text=f"Container {idx}")
        dev_sec = MagicMock()
        dev_sec.node_id = 10 + idx
        dev_sec.individual_address = f"1.1.{10 + idx}"
        dev_sec.get_ui.return_value = [tab_sec]

        res_sec = _scan_device_slots(dev_sec)
        assert len(res_sec["slots"]) == 0, (
            f"Expected 0 slots for secondary parameter {lbl!r}, got {len(res_sec['slots'])}"
        )


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_scan_device_slots_with_real_fixture(tmp_path: Path) -> None:
    """project_find_free_slots scans channels on a real device fixture."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        await _data(client, "catalog_import_knxprod", path=str(_FIXTURE))
        product_ref = (await _data(client, "catalog_list_products"))["items"][0][
            "product_ref_id"
        ]
        device = await _data(client, "project_add_device", product_ref_id=product_ref)
        node_id = device["node_id"]

        slots_res = await _data(client, "project_find_free_slots", node_id=node_id)
        assert slots_res["total_slots"] >= 2
        assert (
            slots_res["free_count"] + slots_res["used_count"]
            == slots_res["total_slots"]
        )


READ_ONLY_TOOLS = {
    "catalog_docs",
    "catalog_docs_cached",
    "catalog_get_application",
    "catalog_get_product_details",
    "catalog_list_products",
    "catalog_online_items",
    "catalog_online_language",
    "connection_dali_scan",
    "connection_siemens_dali_scan",
    "connection_status",
    "find_potential_com_objects",
    "keyring_status",
    "monitor_latest",
    "monitor_telegrams",
    "network_cemi_records",
    "network_state",
    "network_telegrams",
    "project_compare_devices",
    "project_compare_projects",
    "project_devices_with_update",
    "project_diff",
    "project_find_free_slots",
    "project_get_com_object",
    "project_get_com_object_links",
    "project_get_comment",
    "project_get_device",
    "project_get_element",
    "project_get_ga_assignments",
    "project_get_group_address",
    "project_get_ip_secure",
    "project_get_parameter",
    "project_history",
    "project_info",
    "project_list_application_programs",
    "project_list_com_objects",
    "project_list_devices",
    "project_list_group_addresses",
    "project_list_group_ranges",
    "project_list_links",
    "project_list_parameters",
    "project_next_free_group_address",
    "project_parameter_tree",
    "project_plugin_parameters",
    "project_resolve_parameter",
    "project_spaces",
    "project_status",
    "project_topology",
    "status",
}

DESTRUCTIVE_TOOLS = {
    "catalog_delete_product",
    "catalog_export_knxprod",
    "connection_assign_individual_address",
    "connection_dali_installation",
    "connection_dali_program_groups",
    "connection_dali_program_scenes",
    "connection_master_reset_device",
    "connection_program_device",
    "connection_restart_device",
    "connection_siemens_dali_program",
    "keyring_clear",
    "keyring_export",
    "monitor_clear",
    "monitor_send_write",
    "network_clear",
    "project_batch",
    "project_change_application",
    "project_compact",
    "project_export_keyring",
    "project_export_knxproj",
    "project_new",
    "project_remove_area",
    "project_remove_device",
    "project_remove_function",
    "project_remove_function_group_address",
    "project_remove_group_address",
    "project_remove_group_range",
    "project_remove_line",
    "project_remove_plugin_item",
    "project_remove_space",
    "project_replace_device",
    "project_unlink_com_object",
    "project_update_application",
}

NON_DESTRUCTIVE_WRITE_TOOLS = {
    "catalog_docs_fetch",
    "catalog_download_online_products",
    "catalog_import_knxprod",
    "catalog_import_knxprod_bytes",
    "catalog_refresh_online_manufacturers",
    "catalog_set_online_language",
    "connection_configure",
    "connection_connect",
    "connection_connect_gateway",
    "connection_dali_identify",
    "connection_disconnect",
    "connection_evaluate_device",
    "connection_read_programming_mode_devices",
    "connection_scan",
    "keyring_load",
    "monitor_read_value",
    "monitor_send_read",
    "network_start",
    "network_stop",
    "project_add_device",
    "project_add_device_certificate",
    "project_add_function_group_address",
    "project_add_plugin_item",
    "project_assign_additional_addresses",
    "project_close",
    "project_copy_device",
    "project_copy_device_configuration",
    "project_create_area",
    "project_create_function",
    "project_create_group_address",
    "project_create_group_addresses_for_com_objects",
    "project_create_group_range",
    "project_create_line",
    "project_create_space",
    "project_extended_copy",
    "project_import_knxproj",
    "project_jump_to",
    "project_link_com_object",
    "project_link_com_objects",
    "project_move_space",
    "project_open",
    "project_redo",
    "project_rename_area",
    "project_rename_device",
    "project_rename_function",
    "project_rename_group_address",
    "project_rename_group_range",
    "project_rename_line",
    "project_rename_space",
    "project_set_additional_address",
    "project_set_com_object_flag",
    "project_set_com_object_properties",
    "project_set_comment",
    "project_set_description",
    "project_set_device_space",
    "project_set_function_type",
    "project_set_group_address_dpt",
    "project_set_group_address_security",
    "project_set_individual_address",
    "project_set_ip_secure",
    "project_set_name",
    "project_set_number",
    "project_set_parameter",
    "project_set_plugin_parameter",
    "project_set_secure_commissioning",
    "project_set_serial_number",
    "project_set_space_type",
    "project_setup_parameters",
    "project_shift_group_addresses",
    "project_shift_individual_addresses",
    "project_undo",
}

OPEN_WORLD_TOOLS = {
    "catalog_docs",
    "catalog_docs_fetch",
    "catalog_download_online_products",
    "catalog_online_items",
    "catalog_refresh_online_manufacturers",
    "connection_assign_individual_address",
    "connection_connect",
    "connection_connect_gateway",
    "connection_dali_identify",
    "connection_dali_installation",
    "connection_dali_program_groups",
    "connection_dali_program_scenes",
    "connection_dali_scan",
    "connection_disconnect",
    "connection_evaluate_device",
    "connection_master_reset_device",
    "connection_program_device",
    "connection_read_programming_mode_devices",
    "connection_restart_device",
    "connection_scan",
    "connection_siemens_dali_program",
    "connection_siemens_dali_scan",
    "monitor_read_value",
    "monitor_send_read",
    "monitor_send_write",
}


async def test_tool_annotations_classification(tmp_path: Path) -> None:
    """Verify that every registered tool is strictly classified into read-only, destructive,
    or non-destructive write, and annotations match exactly."""
    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        tools = await client.list_tools()
        registered_names = {t.name for t in tools}
        classified_names = (
            READ_ONLY_TOOLS | DESTRUCTIVE_TOOLS | NON_DESTRUCTIVE_WRITE_TOOLS
        )

        # 1. No overlap between sets
        assert READ_ONLY_TOOLS.isdisjoint(DESTRUCTIVE_TOOLS)
        assert READ_ONLY_TOOLS.isdisjoint(NON_DESTRUCTIVE_WRITE_TOOLS)
        assert DESTRUCTIVE_TOOLS.isdisjoint(NON_DESTRUCTIVE_WRITE_TOOLS)
        print(
            f"\nTOOL_COUNTS: read_only={len(READ_ONLY_TOOLS)}, destructive={len(DESTRUCTIVE_TOOLS)}, "
            f"non_destructive_write={len(NON_DESTRUCTIVE_WRITE_TOOLS)}, total={len(registered_names)}"
        )

        # 2. Every registered tool must be classified
        unclassified = registered_names - classified_names
        assert not unclassified, f"Unclassified tools registered: {unclassified}"

        # 3. Every classified tool must be registered
        missing = classified_names - registered_names
        assert not missing, f"Classified tools missing from server: {missing}"

        # 4. Check openWorldHint against OPEN_WORLD_TOOLS
        open_world_actual = {
            t.name
            for t in tools
            if (
                t.annotations.model_dump(by_alias=True).get("openWorldHint") is True
                if hasattr(getattr(t, "annotations", None), "model_dump")
                else (
                    t.annotations.get("openWorldHint") is True
                    if isinstance(getattr(t, "annotations", None), dict)
                    else False
                )
            )
        }
        assert open_world_actual == OPEN_WORLD_TOOLS, (
            f"openWorldHint mismatch: diff={open_world_actual ^ OPEN_WORLD_TOOLS}"
        )
        print(
            f"OPEN_WORLD_TOOLS: count={len(open_world_actual)}, tools={sorted(open_world_actual)}"
        )

        # 5. Check annotations on each tool
        for tool in tools:
            name = tool.name
            ann = getattr(tool, "annotations", None)
            if hasattr(ann, "model_dump"):
                ann_dict = ann.model_dump(by_alias=True)
            elif isinstance(ann, dict):
                ann_dict = ann
            else:
                ann_dict = {}

            if name in READ_ONLY_TOOLS:
                assert ann_dict.get("readOnlyHint") is True, (
                    f"{name}: expected readOnlyHint=True, got {ann_dict}"
                )
                assert ann_dict.get("destructiveHint") is False, (
                    f"{name}: expected destructiveHint=False, got {ann_dict}"
                )
            elif name in DESTRUCTIVE_TOOLS:
                assert ann_dict.get("readOnlyHint") is False, (
                    f"{name}: expected readOnlyHint=False, got {ann_dict}"
                )
                assert ann_dict.get("destructiveHint") is True, (
                    f"{name}: expected destructiveHint=True, got {ann_dict}"
                )
            else:
                assert ann_dict.get("readOnlyHint") is False, (
                    f"{name}: expected readOnlyHint=False, got {ann_dict}"
                )
                assert ann_dict.get("destructiveHint") is False, (
                    f"{name}: expected destructiveHint=False, got {ann_dict}"
                )

            if name in OPEN_WORLD_TOOLS:
                assert ann_dict.get("openWorldHint") is True, (
                    f"{name}: expected openWorldHint=True, got {ann_dict}"
                )
            else:
                assert ann_dict.get("openWorldHint") is False, (
                    f"{name}: expected openWorldHint=False, got {ann_dict}"
                )


async def test_mcp_instruction_and_docstring_references(tmp_path: Path) -> None:
    """Verify that every tool referenced in INSTRUCTIONS and tool docstrings actually exists in list_tools(),
    and that INSTRUCTIONS length stays strictly below 1500 characters."""
    from editor_gui.plugins.mcp.server import INSTRUCTIONS

    assert len(INSTRUCTIONS) <= 1500, (
        f"INSTRUCTIONS length {len(INSTRUCTIONS)} exceeds 1500 chars limit"
    )

    mcp, _project = _build(tmp_path)
    async with Client(mcp) as client:
        tools = await client.list_tools()
        registered_names = {t.name for t in tools}

    pattern = re.compile(r"\b(project|catalog|connection|monitor|network)_[a-z0-9_]+\b")

    # 1. Check INSTRUCTIONS
    for match in pattern.finditer(INSTRUCTIONS):
        tool_ref = match.group(0)
        assert tool_ref in registered_names, (
            f"Unknown tool {tool_ref!r} referenced in INSTRUCTIONS"
        )

    # 2. Check each tool's description
    known_non_tools = {"project_password", "catalog_items", "catalog_item_ids"}
    unknown_refs: list[tuple[str, str]] = []
    for tool in tools:
        desc = tool.description or ""
        for match in pattern.finditer(desc):
            tool_ref = match.group(0)
            if tool_ref in known_non_tools:
                continue
            if tool_ref not in registered_names:
                unknown_refs.append((tool.name, tool_ref))
    assert not unknown_refs, (
        f"Unknown tool references found in descriptions: {unknown_refs}"
    )
