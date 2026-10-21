"""Tests for the online catalog client (parsers, cache) and the panel fetch state."""

import json

import pytest

from editor_gui.plugins.catalog import online_catalog as oc
from editor_gui.plugins.catalog.online_catalog import (
    OnlineCatalogClient,
    OnlineCatalogError,
    OnlineCatalogItem,
    OnlineManufacturer,
    parse_catalog_items,
    parse_manufacturer_ids,
    parse_manufacturer_names,
    search_index,
)

_MANUFACTURERS_XML = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<ArrayOfunsignedShort xmlns="http://schemas.microsoft.com/2003/10/Serialization/Arrays">'
    b"<unsignedShort>2</unsignedShort>"
    b"<unsignedShort>1</unsignedShort>"
    b"<unsignedShort>10</unsignedShort>"
    b"</ArrayOfunsignedShort>"
)

_MASTER_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<KNX xmlns="http://knx.org/xml/project/23">'
    b'<MasterData Id="MD-1" Version="1">'
    b'<Manufacturer Id="M-0001" KnxManufacturerId="1" Name="Siemens" />'
    b'<Manufacturer Id="M-0002" KnxManufacturerId="2" Name="ABB" />'
    b"<Manufacturers></Manufacturers>"
    b"</MasterData>"
    b"</KNX>"
)


class TestParse:
    def test_manufacturer_ids_sorted_and_namespaced(self) -> None:
        assert parse_manufacturer_ids(_MANUFACTURERS_XML) == [1, 2, 10]

    def test_manufacturer_ids_empty_raises(self) -> None:
        with pytest.raises(OnlineCatalogError):
            parse_manufacturer_ids(b"<ArrayOfunsignedShort></ArrayOfunsignedShort>")

    def test_manufacturer_names(self) -> None:
        assert parse_manufacturer_names(_MASTER_XML) == {1: "Siemens", 2: "ABB"}

    def test_catalog_items_parsed_and_gated(self) -> None:
        index = json.dumps(
            {
                "Entries": [
                    {
                        "Id": "CI-2",
                        "CatalogItemName": "Zeta switch",
                        "OrderNumber": "2000",
                        "NoDownloadWithoutPlugin": False,
                    },
                    {
                        "Id": "CI-1",
                        "ProductName": "Alpha dimmer",
                        "OrderNumber": "1000",
                        "RequiresExternalSoftware": True,
                    },
                    {"NoId": "skipme"},
                ]
            }
        ).encode()
        items = parse_catalog_items(index)
        # sorted by display name; entry without an Id is skipped
        assert [i.id for i in items] == ["CI-1", "CI-2"]
        assert items[0].name == "Alpha dimmer" and not items[0].downloadable
        assert items[1].name == "Zeta switch" and items[1].downloadable
        assert items[1].order_number == "2000"

    def test_catalog_items_no_market_offers_are_not_downloadable(self) -> None:
        # The server refuses items no market lists ("NotInAnyMarket").
        index = json.dumps(
            {
                "Entries": [
                    {"Id": "CI-1", "CatalogItemName": "Offered"},
                    {"Id": "CI-2", "CatalogItemName": "Withdrawn"},
                ],
                "Markets": [
                    {"DefaultName": "Germany", "CatalogIds": ["CI-1"]},
                    {"DefaultName": "Austria", "CatalogIds": []},
                ],
            }
        ).encode()
        offered, withdrawn = parse_catalog_items(index)
        assert offered.downloadable and not offered.download_block_reason
        assert not withdrawn.downloadable
        assert withdrawn.download_block_reason == "not offered in any market"

    def test_catalog_items_without_markets_stay_downloadable(self) -> None:
        index = json.dumps({"Entries": [{"Id": "CI-1", "CatalogItemName": "A"}]})
        assert parse_catalog_items(index.encode())[0].downloadable

    def test_catalog_items_captures_manufacturer_and_version(self) -> None:
        # ApplicationIdentifier is the online index's list form: [_, mfr, _, appNo, version].
        index = json.dumps(
            {
                "ManufacturerId": 131,
                "ManufacturerName": "MDT",
                "Entries": [
                    {
                        "Id": "CI",
                        "CatalogItemName": "AKD-0424R.02",
                        "OrderNumber": "AKD-0424R.02",
                        "ApplicationIdentifier": [0, 131, 0, 64, 40],
                        "ApplicationProgramName": "Dimmen 4fach",
                    }
                ],
            }
        ).encode()
        item = parse_catalog_items(index)[0]
        assert item.manufacturer_id == 131
        assert item.manufacturer_name == "MDT"
        assert item.application_version == 40
        assert item.application_program_name == "Dimmen 4fach"

    def test_app_version_from_list_and_string(self) -> None:
        from editor_gui.plugins.catalog.online_catalog import _app_version

        assert _app_version([0, 131, 0, 64, 40]) == 40  # online list form
        assert _app_version("M-0083_A-0040-24-52B5") == 0x24  # string id (hex segment)
        assert _app_version("bad") is None
        assert _app_version(None) is None

    def test_catalog_items_invalid_json_raises(self) -> None:
        with pytest.raises(OnlineCatalogError):
            parse_catalog_items(b"not json")

    def test_manufacturer_names_empty_raises(self) -> None:
        with pytest.raises(OnlineCatalogError):
            parse_manufacturer_names(b"<KNX><MasterData></MasterData></KNX>")


class TestClient:
    def test_cache_roundtrip(self, tmp_path) -> None:
        client = OnlineCatalogClient(tmp_path)
        assert client.cached_manufacturers() is None

        client._save_cache([OnlineManufacturer(1, "Siemens")])
        assert [m.name for m in client.cached_manufacturers()] == ["Siemens"]

    def test_cache_ignores_corrupt_file(self, tmp_path) -> None:
        (tmp_path / "online_catalog_manufacturers.json").write_text("not json", "utf-8")
        assert OnlineCatalogClient(tmp_path).cached_manufacturers() is None

    def test_cache_survives_reload(self, tmp_path) -> None:
        OnlineCatalogClient(tmp_path)._save_cache(
            [OnlineManufacturer(7, "Berker"), OnlineManufacturer(1, "Siemens")]
        )
        reloaded = OnlineCatalogClient(tmp_path).cached_manufacturers()
        assert [m.id for m in reloaded] == [7, 1]
        assert json.loads(
            (tmp_path / "online_catalog_manufacturers.json").read_text("utf-8")
        ) == [{"id": 7, "name": "Berker"}, {"id": 1, "name": "Siemens"}]


class TestSearchIndex:
    def _idx(self) -> dict[int, list[OnlineCatalogItem]]:
        return {
            1: [
                OnlineCatalogItem(
                    "a", "AKD-0424R.02 LED", "AKD-0424R.02", True, 1, "MDT"
                ),
                OnlineCatalogItem("b", "Glass switch", "BE-GT2", True, 1, "MDT"),
            ],
            2: [OnlineCatalogItem("c", "clone AKD", "XX-AKD-9", True, 2, "Other")],
        }

    def test_order_number_prefix_ranks_first(self) -> None:
        res, total = search_index(self._idx(), "akd")
        assert total == 2  # AKD-0424R.02 (order prefix) and XX-AKD-9 (order contains)
        assert res[0].order_number == "AKD-0424R.02"

    def test_specific_order_number(self) -> None:
        res, total = search_index(self._idx(), "AKD-0424")
        assert total == 1 and res[0].id == "a"

    def test_empty_query(self) -> None:
        assert search_index(self._idx(), "  ") == ([], 0)

    def test_cap_reports_total(self) -> None:
        big = {9: [OnlineCatalogItem(str(i), "node", "ON", True) for i in range(10)]}
        res, total = search_index(big, "node", limit=3)
        assert len(res) == 3 and total == 10


class TestIndexCache:
    def _index_json(self, mid: int) -> bytes:
        return json.dumps(
            {
                "ManufacturerId": mid,
                "ManufacturerName": f"M{mid}",
                "Entries": [
                    {
                        "Id": f"CI-{mid}",
                        "CatalogItemName": f"Prod{mid}",
                        "OrderNumber": f"ON-{mid}",
                        "ApplicationIdentifier": f"M-{mid:04d}_A-1",
                        "ApplicationProgramName": "App",
                    }
                ],
            }
        ).encode()

    def test_refresh_builds_and_persists(self, tmp_path, monkeypatch) -> None:
        # manufacturers list -> ids 1,2,10 (from the shared XML)
        monkeypatch.setattr(oc, "_http_get", lambda url: _MANUFACTURERS_XML)
        calls: list[int] = []
        client = OnlineCatalogClient(tmp_path)
        monkeypatch.setattr(
            client,
            "download_index",
            lambda mid: (calls.append(mid), self._index_json(mid))[1],
        )
        status = client.refresh_index()
        assert status.manufacturers == 3 and status.products == 3
        assert sorted(calls) == [1, 2, 10]
        # persisted to disk
        assert (tmp_path / "online_catalog_index.json").exists()

    def test_refresh_is_resumable(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(oc, "_http_get", lambda url: _MANUFACTURERS_XML)
        c1 = OnlineCatalogClient(tmp_path)
        monkeypatch.setattr(c1, "download_index", lambda mid: self._index_json(mid))
        c1.refresh_index()
        # a fresh client loads the cache; a second refresh downloads nothing (all cached)
        c2 = OnlineCatalogClient(tmp_path)
        calls: list[int] = []
        monkeypatch.setattr(
            c2,
            "download_index",
            lambda mid: (calls.append(mid), self._index_json(mid))[1],
        )
        status = c2.refresh_index()
        assert calls == []
        assert status.products == 3

    def test_cache_of_an_older_format_is_rebuilt(self, tmp_path) -> None:
        # Older caches lack the market check: they are ignored, so the index is built again.
        (tmp_path / "online_catalog_index.json").write_text(
            json.dumps({"fetched_at": "2026-10-17T15:56:00+00:00", "index": {"1": []}}),
            "utf-8",
        )
        assert OnlineCatalogClient(tmp_path).cached_index() is None

    def test_search_via_cached_index(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(oc, "_http_get", lambda url: _MANUFACTURERS_XML)
        client = OnlineCatalogClient(tmp_path)
        monkeypatch.setattr(client, "download_index", lambda mid: self._index_json(mid))
        client.refresh_index()
        res, total = search_index(client.cached_index() or {}, "ON-2")
        assert total == 1 and res[0].id == "CI-2"


class TestPanelOnlineState:
    """The panel's fetch flag logic (imgui rendering is exercised by the app itself)."""

    def test_refresh_flag_lifecycle(self, tmp_path) -> None:
        from editor_gui.plugins.catalog.ui.panel import CatalogPanel

        panel = CatalogPanel(
            get_products=list,
            on_select=lambda product: None,
            get_online_manufacturers=lambda: None,
            on_online_refresh=lambda: None,
        )
        assert not panel._online_shown
        assert not panel._online_loading

        panel._fetch_online()  # success path sets shown, clears loading
        assert panel._online_shown
        assert not panel._online_loading

        def failing_refresh() -> None:
            raise OnlineCatalogError("no network")

        panel._on_online_refresh = failing_refresh
        panel._fetch_online()  # error path keeps the list hidden, clears loading
        assert panel._online_error
        assert panel._online_shown  # a previous successful list stays visible


class TestHttpErrorDetail:
    """A 4xx response body (where the server explains the failure, e.g. a DownloadProduct 400)
    must survive into the raised error instead of being reduced to a bare 'Bad Request' (#51)."""

    def test_http_post_includes_status_and_server_body(self, monkeypatch) -> None:
        import io
        import urllib.error
        import urllib.request

        def _raise(request, timeout=None):
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "Bad Request",
                {},
                io.BytesIO(b"product requires an external plugin"),
            )

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        with pytest.raises(OnlineCatalogError) as excinfo:
            oc._http_post("https://example.test/Download/DownloadProduct", b"{}")
        message = str(excinfo.value)
        assert "400" in message
        assert "Bad Request" in message
        assert "product requires an external plugin" in message


class TestDownloadProductUnwrap:
    """The DownloadProduct response is a ZIP wrapping the real archive as 'products.knxprod' (plus
    id-mapping.xml) - verified against the decompiled ETS downloader. The inner .knxprod must be
    extracted before import (#51)."""

    def _zip(self, entries: dict[str, bytes]) -> bytes:
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        return buf.getvalue()

    def test_wrapper_zip_yields_inner_knxprod(self) -> None:
        inner = self._zip({"knx_master.xml": b"<KNX/>", "M-0001/Hardware.xml": b"<x/>"})
        wrapper = self._zip({"products.knxprod": inner, "id-mapping.xml": b"<m/>"})
        assert oc._unwrap_products_knxprod(wrapper) == inner

    def test_bare_knxprod_passed_through(self) -> None:
        bare = self._zip({"knx_master.xml": b"<KNX/>", "M-0001/Hardware.xml": b"<x/>"})
        assert oc._unwrap_products_knxprod(bare) == bare

    def test_non_zip_passed_through(self) -> None:
        assert oc._unwrap_products_knxprod(b"not a zip") == b"not a zip"
