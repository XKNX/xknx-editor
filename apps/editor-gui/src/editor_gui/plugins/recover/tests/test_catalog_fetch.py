"""Missing recovery applications are fetched once and checked against imported layouts."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from editor_gui.plugins.catalog.alternative import AlternativeCatalogError
from editor_gui.plugins.recover.service import RecoverEntry, RecoverService
from xknxeditor.recover import AppId

from .test_matching import Catalog, master_data, product


class Alternative:
    def __init__(self) -> None:
        self.ids = ["one"]
        self.downloads: list[str] = []
        self.queries: list[tuple[Any, ...]] = []
        self.refresh_error: Exception | None = None
        self.download_error: Exception | None = None

    def load_cache(self) -> None:
        pass

    def refresh(self) -> None:
        if self.refresh_error:
            raise self.refresh_error

    def find_application(self, *args: Any) -> list[Any]:
        self.queries.append(args)
        return [SimpleNamespace(id=key) for key in self.ids]

    def download(self, key: str) -> bytes:
        self.downloads.append(key)
        if self.download_error:
            raise self.download_error
        return key.encode()


class FetchCatalog(Catalog):
    def __init__(self) -> None:
        super().__init__()
        self.alternative = Alternative()
        self.io_lock = threading.RLock()
        self.imports: list[bytes] = []
        self.official: list[int] = []

    def import_knxprod_data(self, data: bytes) -> None:
        self.imports.append(data)
        self.items.append(product(data.decode(), ref="same"))

    def online_catalog_items(self, manufacturer_id: int) -> list[Any]:
        self.official.append(manufacturer_id)
        return []


def setup() -> tuple[RecoverService, FetchCatalog]:
    catalog = FetchCatalog()
    service = RecoverService(
        SimpleNamespace(
            catalog=catalog, connection=SimpleNamespace(master=master_data())
        )
    )
    entry = RecoverEntry("1.1.5", 0x0013, app_id=AppId("M-0002", 0x6322, 0x23))
    service.entries = [entry, replace(entry, address="1.1.6")]
    for e in service.entries:
        service._identify_catalog(e)
    return service, catalog


def test_download_dedup_and_complete_candidate_set() -> None:
    service, catalog = setup()
    catalog.alternative.ids = ["one", "two"]
    service._autofetch()
    assert catalog.alternative.downloads == ["one", "two"]
    assert catalog.imports == [b"one", b"two"]
    assert catalog.official == []
    assert all(
        e.candidates == 2 and e.ambiguous and not e.selected for e in service.entries
    )
    assert all(e.match_reason == "compatible_mask" for e in service.entries)
    assert (
        service._catalog_changed
        and not service.fetch_status
        and not service.fetch_error
    )
    assert catalog.alternative.queries[0] == (
        "M-0002",
        0x6322,
        0x23,
        ("MV-0013", "MV-0011", "MV-0012"),
    )


def test_different_version_does_not_hide_missing_exact_application() -> None:
    service, catalog = setup()
    catalog.items.append(product("old", version=0x22, mask="MV-0013"))
    service._reidentify_unresolved()
    assert all(e.match_reason == "different_version" for e in service.entries)
    service._autofetch()
    assert all(e.match_reason == "compatible_mask" for e in service.entries)
    assert catalog.official == []


def test_failed_refresh_can_use_cached_entries() -> None:
    service, catalog = setup()
    catalog.alternative.refresh_error = AlternativeCatalogError("offline")
    service._autofetch()
    assert catalog.imports == [b"one"]
    assert "offline" in service.fetch_error


@pytest.mark.parametrize(
    "error",
    [
        AlternativeCatalogError("not approved", status=403),
        AlternativeCatalogError("limit", status=429, retry_after=60),
        AlternativeCatalogError("daily limit", code="DOWNLOAD_DAILY_LIMIT"),
    ],
)
def test_auth_and_limit_errors_stop_repeated_downloads_but_allow_official(
    error: Exception,
) -> None:
    service, catalog = setup()
    catalog.alternative.ids = ["one", "two"]
    catalog.alternative.download_error = error
    service._autofetch()
    assert catalog.alternative.downloads == ["one"]
    assert catalog.imports == [] and catalog.official == [2]
    assert str(error) in service.fetch_error
    assert not service.busy and service.fetch_status == ""


def test_missing_login_explains_the_catalog_match_and_next_step() -> None:
    service, catalog = setup()
    catalog.alternative.ids = ["one", "two"]
    catalog.alternative.download_error = AlternativeCatalogError(
        "configure login", code="NO_ACCOUNT"
    )
    service._autofetch()
    assert catalog.alternative.downloads == ["one"]
    assert catalog.imports == [] and catalog.official == [2]
    assert service.fetch_error == (
        "The application version missing from the local catalog was found in the "
        "alternative catalog. Downloading it needs an account for the alternative "
        "catalog. Register one, or enter your username and password, in Settings > "
        "Catalog."
    )
    assert not service.busy and service.fetch_status == ""


def test_no_match_uses_official_catalog() -> None:
    service, catalog = setup()
    catalog.alternative.ids = []
    service._autofetch()
    assert not catalog.alternative.downloads
    assert catalog.official == [2]
    assert service.fetch_error == ""


def test_imported_metadata_is_rechecked_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, catalog = setup()
    monkeypatch.setattr(
        catalog,
        "import_knxprod_data",
        lambda data: catalog.items.append(product(medium="MT-1")),
    )
    service._autofetch()
    assert not any(e.recoverable for e in service.entries)
    assert catalog.official == [2]


def test_cancel_after_download_prevents_import_and_further_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, catalog = setup()

    def download(key: str) -> bytes:
        service.stop()
        return b"one"

    monkeypatch.setattr(catalog.alternative, "download", download)
    service._autofetch()
    assert catalog.imports == [] and catalog.official == []
    service._start_autofetch()
    assert not service.busy


def test_existing_local_application_does_not_trigger_network() -> None:
    service, catalog = setup()
    catalog.items.append(product())
    service._reidentify_unresolved()
    service._start_autofetch()
    assert not service.busy and not catalog.alternative.queries


def test_reidentification_publishes_complete_entries_without_mutating_displayed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, catalog = setup()
    catalog.items.extend(
        [product("old-one", version=0x22), product("old-two", version=0x22)]
    )
    service._reidentify_unresolved()
    displayed = service.entries
    choices = [entry.catalog_candidates for entry in displayed]
    states = [entry.state for entry in displayed]
    lookup = catalog.get_application

    def read_application(app_id: str) -> Any:
        assert service.entries is displayed
        assert [entry.state for entry in displayed] == states
        assert [entry.catalog_candidates for entry in displayed] == choices
        assert all(len(entry.catalog_candidates) == 2 for entry in displayed)
        return lookup(app_id)

    catalog.items.append(product("new"))
    monkeypatch.setattr(catalog, "get_application", read_application)
    service._reidentify_unresolved()
    assert service.entries is not displayed
    assert all(entry.candidates == 1 and entry.selected for entry in service.entries)
    assert all(entry.candidates == 2 and not entry.selected for entry in displayed)
    assert all(
        new is not old for new, old in zip(service.entries, displayed, strict=True)
    )


class OnlineCatalog(FetchCatalog):
    """The online catalog of M-0002 with products of three applications; ``refused`` products
    make a request fail as the server does when one of them is offered in no market."""

    def __init__(self) -> None:
        super().__init__()
        self.alternative.ids = []
        self.requests: list[list[str]] = []
        self.refused: set[str] = set()

    def refresh(self) -> None:
        pass

    def online_catalog_items(self, manufacturer_id: int) -> list[Any]:
        self.official.append(manufacturer_id)
        return [
            online_item(
                item_id,
                downloadable=downloadable,
                number=number,
                version=version,
            )
            for item_id, downloadable, number, version in (
                ("wanted", True, 0x6322, 0x23),
                ("wanted-too", True, 0x6322, 0x23),
                ("other-version", True, 0x6322, 0x20),
                ("other-application", True, 0x6326, 0x23),
                ("not-offered", False, 0x6322, 0x23),
            )
        ]

    def download_online_products(self, item_ids: list[str]) -> list[Any]:
        from editor_gui.plugins.catalog.online_catalog import OnlineCatalogError

        self.requests.append(list(item_ids))
        if self.refused & set(item_ids):
            raise OnlineCatalogError("HTTP 400: Bad Request")
        return [object() for _ in item_ids]


def _online_setup() -> tuple[RecoverService, OnlineCatalog]:
    catalog = OnlineCatalog()
    service = RecoverService(
        SimpleNamespace(
            catalog=catalog, connection=SimpleNamespace(master=master_data())
        )
    )
    service.entries = [
        RecoverEntry("1.1.5", 0x0013, app_id=AppId("M-0002", 0x6322, 0x23))
    ]
    for e in service.entries:
        service._identify_catalog(e)
    return service, catalog


def test_only_the_devices_applications_are_downloaded() -> None:
    service, catalog = _online_setup()
    service._autofetch()
    assert catalog.official == [2]
    assert catalog.requests == [["wanted", "wanted-too"]]
    assert service.fetch_error == ""


def test_a_refused_request_is_tried_product_by_product() -> None:
    service, catalog = _online_setup()
    catalog.refused = {"wanted-too"}
    service._autofetch()
    assert catalog.requests == [["wanted", "wanted-too"], ["wanted"], ["wanted-too"]]
    # The product the server refuses is reported, the other one is imported.
    assert "Bad Request" in service.fetch_error
    assert service._catalog_changed


def test_no_product_of_the_application_downloads_nothing() -> None:
    service, catalog = _online_setup()
    service.entries = [
        replace(service.entries[0], app_id=AppId("M-0002", 0x6322, 0x10))
    ]
    service._autofetch()
    assert catalog.requests == []
    assert service.fetch_error == ""


@pytest.mark.parametrize("refused,added", [(set(), 2), ({"wanted-too"}, 1)])
def test_download_counts_successful_product_versions(
    refused: set[str], added: int
) -> None:
    service, catalog = _online_setup()
    catalog.refused = refused
    assert service._download_online("M-0002", ["wanted", "wanted-too"]) == added


def online_item(
    item_id: str,
    number: int = 0x6322,
    version: int = 0x23,
    manufacturer: int = 2,
    downloadable: bool = True,
) -> Any:
    from editor_gui.plugins.catalog.online_catalog import OnlineCatalogItem

    return OnlineCatalogItem(
        item_id,
        item_id,
        item_id,
        downloadable,
        manufacturer_id=manufacturer,
        application_manufacturer_id=manufacturer,
        application_number=number,
        application_version=version,
    )


def official_fetch(service: RecoverService) -> None:
    from editor_gui.plugins.catalog.online_catalog import OnlineCatalogError

    service._fetch_manufacturer(
        service.entries[0].app_id.manufacturer_id, OnlineCatalogError
    )
    service._reidentify_unresolved()


def test_official_fetch_selects_only_exact_application_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, catalog = setup()
    items = [
        online_item("matching-a"),
        online_item("matching-a"),
        online_item("matching-b"),
        online_item("wrong-number", number=0x6323),
        online_item("wrong-version", version=0x24),
        online_item("wrong-manufacturer", manufacturer=7),
        online_item("no-market", downloadable=False),
    ]
    monkeypatch.setattr(catalog, "online_catalog_items", lambda mid: items)
    downloads: list[list[str]] = []

    def download(ids: list[str]) -> list[Any]:
        downloads.append(ids)
        catalog.items.extend(product(item_id) for item_id in ids)
        return []

    monkeypatch.setattr(catalog, "download_online_products", download, raising=False)
    monkeypatch.setattr(catalog, "refresh", lambda: None, raising=False)
    official_fetch(service)
    assert downloads == [["matching-a", "matching-b"]]
    assert all(e.candidates == 2 and e.ambiguous for e in service.entries)
    assert service._catalog_changed


@pytest.mark.parametrize("number", [0xA01E, 0xA020, 0xA023, 0xA03A, 0xA03B])
def test_catalog_version_two_does_not_trigger_download_for_version_one(
    number: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, catalog = setup()
    service.entries[0].app_id = AppId("M-0002", number, 0x10)
    service.entries = service.entries[:1]
    monkeypatch.setattr(
        catalog,
        "online_catalog_items",
        lambda mid: [online_item("newer", number, 0x20)],
    )
    official_fetch(service)
    assert not service._catalog_changed and not service.fetch_error


def test_panel_identifier_does_not_download_unrelated_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, catalog = setup()
    service.entries = [RecoverEntry("1.1.9", 0x0013, app_id=AppId("M-0007", 0, 0xFF))]
    monkeypatch.setattr(
        catalog,
        "online_catalog_items",
        lambda mid: [
            online_item("accessory", 0, 0, 7),
            online_item("panel", 0x6300, 0x1A, 7),
        ],
    )
    official_fetch(service)
    assert not service._catalog_changed and not service.fetch_error


def test_market_rejection_does_not_block_other_application_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from editor_gui.plugins.catalog.online_catalog import OnlineCatalogError

    service, catalog = setup()
    monkeypatch.setattr(
        catalog,
        "online_catalog_items",
        lambda mid: [online_item("a"), online_item("b")],
    )
    downloads: list[list[str]] = []

    def download(ids: list[str]) -> list[Any]:
        downloads.append(ids)
        if "a" in ids:
            raise OnlineCatalogError('400 Bad Request: {"Id":"NotInAnyMarket"}')
        catalog.items.append(product("b"))
        return []

    monkeypatch.setattr(catalog, "download_online_products", download, raising=False)
    monkeypatch.setattr(catalog, "refresh", lambda: None, raising=False)
    official_fetch(service)
    assert downloads == [["a", "b"], ["a"], ["b"]]
    assert "NotInAnyMarket" in service.fetch_error
    assert all(e.recoverable for e in service.entries)


def test_stop_between_official_products_keeps_imported_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from editor_gui.plugins.catalog.online_catalog import OnlineCatalogError

    service, catalog = setup()
    monkeypatch.setattr(
        catalog,
        "online_catalog_items",
        lambda mid: [online_item("a"), online_item("b")],
    )
    downloads: list[list[str]] = []

    def download(ids: list[str]) -> list[Any]:
        downloads.append(ids)
        if len(ids) > 1:
            raise OnlineCatalogError("batch refused")
        catalog.items.append(product("a"))
        service.stop()
        return []

    monkeypatch.setattr(catalog, "download_online_products", download, raising=False)
    monkeypatch.setattr(catalog, "refresh", lambda: None, raising=False)
    official_fetch(service)
    assert downloads == [["a", "b"], ["a"]]
    assert all(e.recoverable for e in service.entries)


@pytest.mark.parametrize(
    "manufacturer,number,version",
    [(2, 0xA01E, 0x20), (7, 0x0806, 0x71), (0x183, 0x1234, 0x2A)],
)
def test_index_identity_matches_the_identity_read_from_the_device(
    manufacturer: int, number: int, version: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    from editor_gui.plugins.catalog.online_catalog import parse_catalog_items
    from xknxeditor.recover.identify import parse_application_id

    service, catalog = setup()
    manufacturer_id = f"M-{manufacturer:04X}"
    identity = [
        manufacturer >> 8,
        manufacturer & 0xFF,
        number >> 8,
        number & 0xFF,
        version,
    ]
    app_id = parse_application_id(bytes(identity))
    assert app_id is not None
    service.entries = [RecoverEntry("1.1.5", 0x0013, app_id=app_id)]
    items = parse_catalog_items(
        json.dumps(
            {
                "ManufacturerId": manufacturer,
                "Entries": [
                    {"Id": "match", "ApplicationIdentifier": identity},
                    {
                        "Id": "wrong-manufacturer",
                        "ApplicationIdentifier": [
                            0,
                            1,
                            identity[2],
                            identity[3],
                            version,
                        ],
                    },
                ],
            }
        ).encode()
    )
    monkeypatch.setattr(catalog, "online_catalog_items", lambda mid: items)
    downloads = []

    def download(ids):
        downloads.append(ids)
        catalog.items.append(
            product(
                "match", manufacturer=manufacturer_id, number=number, version=version
            )
        )
        return []

    monkeypatch.setattr(catalog, "download_online_products", download, raising=False)
    monkeypatch.setattr(catalog, "refresh", lambda: None, raising=False)
    official_fetch(service)
    assert downloads == [["match"]]
    assert service.entries[0].recoverable and service.entries[0].selected


def test_failed_single_import_does_not_stop_the_next_manufacturer(monkeypatch) -> None:
    service, catalog = setup()
    catalog.alternative.ids = []
    service.entries[1].app_id = AppId("M-0007", 0x6322, 0x23)
    monkeypatch.setattr(
        catalog,
        "online_catalog_items",
        lambda mid: [online_item(f"article-{mid}", manufacturer=mid)],
    )
    downloads = []

    def download(ids):
        downloads.append(ids)
        if ids == ["article-2"]:
            raise ValueError("invalid product archive")
        catalog.items.append(product("valid", manufacturer="M-0007"))
        return []

    monkeypatch.setattr(catalog, "download_online_products", download, raising=False)
    monkeypatch.setattr(catalog, "refresh", lambda: None, raising=False)
    service._autofetch()
    assert downloads == [["article-2"], ["article-7"]]
    assert "article-2: invalid product archive" in service.fetch_error
    assert not service.entries[0].recoverable
    assert service.entries[1].recoverable and service.entries[1].selected
