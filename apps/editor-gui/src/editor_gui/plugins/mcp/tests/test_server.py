"""End-to-end tests for the embedded MCP server against the live GUI services (headless).

The GUI services are imgui-free, so we build them directly, wire a synchronous ``run_on_ui`` (tools
run inline instead of on a real imgui thread), and drive the tools through FastMCP's in-memory
``Client``. Bus tools are not exercised (no gateway); this covers the project/catalog DATA surface.
"""

from __future__ import annotations

import io
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
