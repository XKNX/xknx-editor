"""Other versions are imported deliberately and remain distinct from the observed identity."""

from __future__ import annotations

from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from editor_gui.plugins.catalog.online_catalog import OnlineCatalogError
from editor_gui.plugins.recover.matching import OnlineVersion
from xknxeditor.recover import (
    AppId,
    RecoveredDevice,
    RecoveredParameters,
    devices_from_snapshot,
    snapshots_json,
)

from .test_catalog_fetch import official_fetch, online_item, setup
from .test_matching import Catalog, identify, master_data, product


def recovered(application_id: str) -> RecoveredDevice:
    return RecoveredDevice(
        address="1.1.5",
        application_id=application_id,
        device_address=0x1105,
        group_addresses=[],
        links=[],
        group_objects={},
        parameters=RecoveredParameters(values={}, unknown=[]),
        mask_version=0x0013,
        parameter_segments={},
        property_values={},
        observed_app_id=AppId("M-0002", 0x6322, 0x23),
    )


def test_only_other_versions_of_the_same_application_are_offered(monkeypatch) -> None:
    service, catalog = setup()
    items = [
        online_item("v25", version=0x25),
        online_item("v24", version=0x24),
        online_item("v24", version=0x24),
        online_item("unavailable", version=0x24, downloadable=False),
        online_item("other-number", number=0x1000, version=0x24),
        online_item("other-manufacturer", manufacturer=7, version=0x24),
    ]
    monkeypatch.setattr(catalog, "online_catalog_items", lambda mid: items)
    official_fetch(service)
    assert not service._catalog_changed
    for entry in service.entries:
        assert [v.catalog_item_id for v in entry.online_versions] == ["v24", "v25"]
        assert entry.other_versions_available and not entry.selected


def test_chosen_article_is_imported_without_selecting_any_device(monkeypatch) -> None:
    service, catalog = setup()
    monkeypatch.setattr(
        catalog, "online_catalog_items", lambda mid: [online_item("v24", version=0x24)]
    )
    official_fetch(service)
    downloads = []

    def download(ids):
        downloads.append(ids)
        catalog.items.append(product("v24", version=0x24))
        return []

    monkeypatch.setattr(catalog, "download_online_products", download, raising=False)
    option = service.entries[0].online_versions[0]
    service._load_other_version("1.1.5", option)
    assert downloads == [["v24"]]
    assert service._catalog_changed and not service.busy
    assert service.phase == "idle"
    for entry in service.entries:
        assert entry.recoverable and entry.version_mismatch
        assert not entry.selected and entry.recovered is None
        assert entry.online_versions == ()


def test_imported_product_is_checked_against_actual_device_mask(monkeypatch) -> None:
    service, catalog = setup()
    option = OnlineVersion("v24", "Actuator", "ABC", 0x24)
    service.entries[0].online_versions = (option,)
    monkeypatch.setattr(
        catalog,
        "download_online_products",
        lambda ids: catalog.items.append(product(version=0x24, mask="MV-0701")),
        raising=False,
    )
    service._load_other_version("1.1.5", option)
    assert not service.entries[0].recoverable
    assert not service.entries[0].selected


def test_failed_import_keeps_the_offer_for_retry(monkeypatch) -> None:
    service, catalog = setup()
    option = OnlineVersion("v24", "Actuator", "ABC", 0x24)
    service.entries[0].online_versions = (option,)

    def fail(ids):
        raise OnlineCatalogError("not in any market")

    monkeypatch.setattr(catalog, "download_online_products", fail, raising=False)
    service._load_other_version("1.1.5", option)
    assert "not in any market" in service.fetch_error
    assert service.entries[0].online_versions == (option,)
    assert not service.busy and not service.fetch_status


def test_manual_import_rejects_unknown_articles_and_read_devices() -> None:
    service, _ = setup()
    entry = service.entries[0]
    entry.online_versions = (OnlineVersion("v24", "Actuator", "ABC", 0x24),)
    with pytest.raises(ValueError, match="not an offered"):
        service.start_load_other_version(entry.address, "unrelated")
    with pytest.raises(ValueError, match="no other"):
        service.start_load_other_version("1.1.99", "v24")
    service._pending = Future()
    with pytest.raises(ValueError, match="still running"):
        service.start_load_other_version(entry.address, "v24")
    service._pending = None
    entry.recovered = recovered("M-0002_A-6322-24-v24")
    with pytest.raises(ValueError, match="no other"):
        service.start_load_other_version(entry.address, "v24")


def test_bad_product_import_does_not_block_later_articles(monkeypatch) -> None:
    service, catalog = setup()
    monkeypatch.setattr(
        catalog,
        "online_catalog_items",
        lambda mid: [online_item("a"), online_item("b")],
    )
    downloads = []

    def download(ids):
        downloads.append(ids)
        if "a" in ids:
            raise ValueError("invalid product archive")
        catalog.items.append(product("b"))
        return []

    monkeypatch.setattr(catalog, "download_online_products", download, raising=False)
    monkeypatch.setattr(catalog, "refresh", lambda: None, raising=False)
    official_fetch(service)
    assert downloads == [["a", "b"], ["a"], ["b"]]
    assert "a: invalid product archive" in service.fetch_error
    assert all(entry.recoverable for entry in service.entries)


async def test_verification_does_not_remove_the_version_warning_or_auto_apply(
    monkeypatch,
) -> None:
    service, entry = identify(Catalog(product(version=0x24)), master=master_data())
    entry.recovered = recovered(entry.application.id)
    entry.selected = True
    service._api.connection.xknx = object()
    service._api.project = SimpleNamespace(is_open=True)
    verify = AsyncMock(return_value=SimpleNamespace(total_changed_bytes=0))
    monkeypatch.setattr("editor_gui.plugins.recover.service.verify_recovered", verify)
    monkeypatch.setattr("editor_gui.plugins.recover.service._SETTLE_SECONDS", 0)
    await service._verify([entry])
    service._auto_apply()
    assert entry.verify_changed == 0 and entry.version_mismatch
    assert not entry.applied and not entry.auto_tried
    assert "device 2.3, recovery 2.4" in service.entry_detail(entry)


def test_snapshot_keeps_its_layout_when_an_exact_device_version_is_local(
    monkeypatch,
) -> None:
    catalog = Catalog(product("layout", version=0x24), product("installed"))
    service, _ = identify(catalog, master=master_data())
    device = recovered("M-0002_A-6322-24-layout")
    monkeypatch.setattr(service, "_factory_image", lambda entry: None)
    monkeypatch.setattr(
        "editor_gui.plugins.recover.service.seed_dynamic_ui", lambda *a: None
    )
    monkeypatch.setattr(
        "editor_gui.plugins.recover.service.com_object_ref_by_number", lambda *a: {}
    )
    assert service.import_snapshot(snapshots_json([device]), "device.json") == 1
    entry = service.entries[0]
    assert entry.application.id == device.application_id
    assert entry.app_id == device.observed_app_id
    assert entry.version_mismatch and not entry.selected
    assert entry.match_reason == "different_version_compatible_mask"
    assert all(c.match_reason == entry.match_reason for c in entry.catalog_candidates)
    entry.selected = True
    assert (
        devices_from_snapshot(service.snapshot_text())[0].observed_app_id
        == entry.app_id
    )


def test_snapshot_does_not_discard_data_when_its_layout_is_missing() -> None:
    service, _ = identify(
        Catalog(product("different-hash", version=0x24)), master=master_data()
    )
    device = recovered("M-0002_A-6322-24-original")
    assert service.import_snapshot(snapshots_json([device]), "device.json") == 1
    entry = service.entries[0]
    assert entry.recovered == device
    assert not entry.recoverable and not entry.selected
    assert entry.product_ref_id is None and entry.catalog_candidates == []
    assert entry.version_mismatch
    assert "device 2.3, recovery 2.4" in service.entry_detail(entry)


def test_same_version_does_not_get_a_warning() -> None:
    service, entry = identify(Catalog(product()), master=master_data())
    entry.recovered = recovered(entry.application.id)
    assert not entry.version_mismatch and service.version_warning(entry) == ""


def test_snapshot_finds_original_layout_even_if_other_mask_has_a_better_rank(
    monkeypatch,
) -> None:
    catalog = Catalog(
        product("original", version=0x24),
        product("other", version=0x24, mask="MV-0013"),
    )
    service, _ = identify(catalog, master=master_data())
    monkeypatch.setattr(service, "_factory_image", lambda entry: None)
    monkeypatch.setattr(
        "editor_gui.plugins.recover.service.seed_dynamic_ui", lambda *a: None
    )
    monkeypatch.setattr(
        "editor_gui.plugins.recover.service.com_object_ref_by_number", lambda *a: {}
    )
    device = replace(recovered("M-0002_A-6322-24-original"), observed_app_id=None)
    service.import_snapshot(snapshots_json([device]), "legacy.json")
    assert service.entries[0].application.id == device.application_id
    assert not service.entries[0].version_mismatch
