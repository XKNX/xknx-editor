"""Missing recovery applications are fetched once and checked against imported layouts."""

from __future__ import annotations

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
        "alternative catalog. A login is required to download it. Enter your username "
        "and password in Settings > Catalog."
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
