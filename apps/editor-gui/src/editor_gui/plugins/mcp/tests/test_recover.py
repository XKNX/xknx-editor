"""The Recover window over MCP: its state, a scan, the selection, adding the devices read back
to a project, and the snapshot (the bus itself replaced by a finished operation)."""

from __future__ import annotations

import dataclasses
from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

from editor_gui.plugins.mcp.server import build_server
from editor_gui.plugins.recover.service import RecoverEntry, RecoverService
from xknxeditor.recover import (
    AppId,
    RecoveredDevice,
    RecoveredParameters,
    RecoveryEffort,
)

from .test_server import _FIXTURE, _build_ctx, _data


def _server(tmp_path: Path) -> tuple[Any, RecoverService]:
    ctx = _build_ctx(tmp_path)
    ctx.recover = RecoverService(ctx.api)
    return build_server(ctx), ctx.recover


def _fake_bus(
    monkeypatch: pytest.MonkeyPatch, service: RecoverService, result: list[RecoverEntry]
) -> list[str]:
    """A connected bus whose operations end at once with ``result``; the operations begun."""
    begun: list[str] = []
    connection: Any = service._api.connection

    def begin(kind: str, what: str) -> bool:
        begun.append(what)
        return True

    def run_async(coroutine: Any) -> Future[Any]:
        coroutine.close()
        done: Future[Any] = Future()
        done.set_result(result)
        return done

    monkeypatch.setattr(service, "connected", lambda: True)
    # No download of missing products from the online catalog.
    monkeypatch.setattr(service, "_start_autofetch", lambda: None)
    monkeypatch.setattr(connection, "begin_operation", begin)
    monkeypatch.setattr(connection, "end_operation", lambda *_args: None)
    monkeypatch.setattr(connection, "run_async", run_async)
    return begun


async def test_the_state_before_a_scan(tmp_path: Path) -> None:
    mcp, _service = _server(tmp_path)
    async with Client(mcp) as client:
        status = await _data(client, "recover_status")
    assert status["phase"] == "idle"
    assert status["connected"] is False
    assert status["busy"] is False
    assert status["entries"] == []
    assert status["auto_apply"] is True


async def test_a_scan_needs_a_connection_and_a_range(tmp_path: Path) -> None:
    mcp, _service = _server(tmp_path)
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="invalid range"):
            await client.call_tool("recover_scan", {"start": "1.1", "end": "x"})
        with pytest.raises(Exception, match="invalid range"):
            await client.call_tool("recover_scan", {"start": "1.1.9", "end": "1.1.1"})
        with pytest.raises(Exception, match="no KNX connection"):
            await client.call_tool("recover_scan", {"start": "1.1.1", "end": "1.1.9"})


async def test_a_scan_lists_the_devices_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, service = _server(tmp_path)
    found = [
        RecoverEntry(address="1.1.3", mask_version=0x07B0),
        RecoverEntry(
            address="1.1.4",
            mask_version=0x07B0,
            app_id=AppId("M-0083", 0x1234, 0x10),
        ),
    ]
    begun = _fake_bus(monkeypatch, service, found)
    async with Client(mcp) as client:
        status = await _data(client, "recover_scan", start="1.1.1", end="1.1.9")
        assert begun == ["1.1.1..1.1.9"]
        assert status["phase"] == "scanned"
        assert [e["address"] for e in status["entries"]] == ["1.1.3", "1.1.4"]
        second = status["entries"][1]
        assert second["mask_version"] == "0x07b0"
        assert (second["manufacturer_id"], second["application_number"]) == (
            "M-0083",
            0x1234,
        )
        # Without a product in the catalog it cannot be selected.
        with pytest.raises(Exception, match="no product in the catalog"):
            await client.call_tool("recover_select", {"addresses": ["1.1.4"]})
        with pytest.raises(Exception, match="not found by the scan"):
            await client.call_tool("recover_select", {"addresses": ["1.1.7"]})
        deselected = await _data(
            client, "recover_select", addresses=["1.1.3"], selected=False
        )
        assert deselected["entries"][0]["selected"] is False
        with pytest.raises(Exception, match="no recoverable device selected"):
            await client.call_tool("recover_read_back", {})
        with pytest.raises(Exception, match="no device read back"):
            await client.call_tool("recover_verify", {})


async def test_settings_and_stop(tmp_path: Path) -> None:
    mcp, service = _server(tmp_path)
    async with Client(mcp) as client:
        status = await _data(client, "recover_set_auto_apply", enabled=False)
        assert status["auto_apply"] is False and service.auto_apply is False
        await _data(client, "recover_stop")
    assert service._cancel is True


async def test_the_search_effort(tmp_path: Path) -> None:
    mcp, service = _server(tmp_path)
    async with Client(mcp) as client:
        assert (await _data(client, "recover_status"))["search_effort"] == "fast"
        status = await _data(client, "recover_set_search_effort", effort="maximum")
        assert status["search_effort"] == "maximum"
        assert service.effort is RecoveryEffort.MAXIMUM
        with pytest.raises(Exception, match="unknown effort"):
            await client.call_tool("recover_set_search_effort", {"effort": "huge"})
        with pytest.raises(Exception, match="is incomplete"):
            await client.call_tool("recover_search_again", {})


async def test_not_available_without_the_window(tmp_path: Path) -> None:
    mcp = build_server(_build_ctx(tmp_path))
    async with Client(mcp) as client:
        with pytest.raises(Exception, match="not available"):
            await client.call_tool("recover_status", {})


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_devices_read_back_are_added_to_the_project(tmp_path: Path) -> None:
    mcp, service = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "catalog_import_product", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        application = service._api.catalog.get_application(product["application_id"])
        assert application is not None
        recovered = RecoveredDevice(
            address="1.1.3",
            application_id=application.id,
            device_address=0x1103,
            group_addresses=[0x0A03],
            links=[],
            group_objects={},
            parameters=RecoveredParameters(values={}, unknown=[]),
        )
        service.entries = [
            RecoverEntry(
                address="1.1.3",
                mask_version=0x07B0,
                product_ref_id=product["product_ref_id"],
                hardware2program_ref_id=product["hardware2program_ref_id"],
                product_name=product["name"],
                application=application,
                recovered=recovered,
            )
        ]
        service.phase = "recovered"

        with pytest.raises(Exception, match="no project is open"):
            await client.call_tool("recover_add_to_project", {})
        snapshot = await _data(client, "recover_snapshot")
        assert len(snapshot["devices"]) == 1

        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        result = await _data(client, "recover_add_to_project")
        assert (result["added"], result["failed"]) == (1, 0)
        entry = result["entries"][0]
        assert entry["applied"] is True
        assert entry["recovered"]["group_addresses"] == ["1/2/3"]
        devices = (await _data(client, "project_list_devices"))["items"]
        assert [d["individual_address"] for d in devices] == ["1.1.3"]

        # Added once: a second add leaves the device that is already added alone.
        again = await _data(client, "recover_add_to_project")
        assert (again["added"], again["failed"]) == (0, 0)
        assert again["entries"][0]["applied"] is True
        devices = (await _data(client, "project_list_devices"))["items"]
        assert [d["individual_address"] for d in devices] == ["1.1.3"]

        # A new project gets it again.
        fresh = await _data(
            client, "recover_add_to_new_project", path=str(tmp_path / "recovered")
        )
        assert fresh["added"] == 1
        assert fresh["path"].endswith("recovered.xknx")


@pytest.mark.skipif(not _FIXTURE.exists(), reason="knxprod fixture missing")
async def test_an_incomplete_device_is_searched_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp, service = _server(tmp_path)
    async with Client(mcp) as client:
        await _data(client, "catalog_import_product", path=str(_FIXTURE))
        product = (await _data(client, "catalog_list_products"))["items"][0]
        application = service._api.catalog.get_application(product["application_id"])
        assert application is not None
        recovered = RecoveredDevice(
            address="1.1.3",
            application_id=application.id,
            device_address=0x1103,
            group_addresses=[],
            links=[],
            group_objects={},
            parameters=RecoveredParameters(
                values={}, unknown=[], differing_bytes=4, limited=True
            ),
        )
        service.entries = [
            RecoverEntry(
                address="1.1.3",
                mask_version=0x07B0,
                product_ref_id=product["product_ref_id"],
                hardware2program_ref_id=product["hardware2program_ref_id"],
                product_name=product["name"],
                application=application,
                recovered=recovered,
            )
        ]
        service.phase = "recovered"
        await _data(client, "project_new", path=str(tmp_path / "p.xknx"))
        added = await _data(client, "recover_add_to_project")
        entry = added["entries"][0]["recovered"]
        assert (entry["complete"], entry["differing_bytes"]) == (False, 4)
        assert entry["more_effort_may_help"] is True

        def search(
            device: RecoveredDevice,
            _app: object,
            effort: RecoveryEffort,
            _cp: object,
            cancel: object = None,
        ) -> RecoveredDevice:
            return dataclasses.replace(
                device,
                parameters=RecoveredParameters(values={}, unknown=[]),
                effort=effort,
            )

        monkeypatch.setattr("editor_gui.plugins.recover.service.search_again", search)
        await _data(client, "recover_set_search_effort", effort="medium")
        result = await _data(client, "recover_search_again", timeout=30.0)
        assert result["phase"] == "recovered"
        again = result["entries"][0]
        assert again["status"] == "recovered"
        assert again["recovered"]["complete"] is True
        assert again["recovered"]["search_effort"] == "medium"
        # The device added before is replaced, not added a second time.
        devices = (await _data(client, "project_list_devices"))["items"]
        assert [d["individual_address"] for d in devices] == ["1.1.3"]
