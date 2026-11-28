"""The online catalog refuses (400 Bad Request) a download that bundles a product without an
application, such as a power supply, with any other product, while it hands out each of them on
its own: a refused download of several items is repeated per item."""

from __future__ import annotations

import io
import urllib.error
from typing import Any

import pytest

from editor_gui.plugins.catalog import online_catalog
from editor_gui.plugins.catalog.online_catalog import (
    OnlineCatalogError,
    OnlineCatalogHTTPError,
)
from editor_gui.plugins.catalog.service import CatalogService

_POWER_SUPPLY = "M-0083_H-83-1_HP_CI-STV.2D0640.2E02-1"


class _Client:
    """Answers like the service: 400 for a bundle with the power supply, else one archive per
    request, named after the items it holds."""

    def __init__(self, status: int = 400) -> None:
        self.status = status
        self.requests: list[list[str]] = []

    def cached_index(self) -> None:
        return None

    def download_product(self, ids: list[str], languages: list[str]) -> bytes:
        self.requests.append(list(ids))
        if len(ids) > 1 and _POWER_SUPPLY in ids:
            raise OnlineCatalogHTTPError(f"DownloadProduct: {self.status}", self.status)
        return ",".join(ids).encode()


def _service(client: _Client) -> Any:
    service: Any = CatalogService.__new__(CatalogService)
    service._online_client = client
    service._language = "de-DE"
    service._browsed = {}
    service.imported = []

    def _import(data: bytes) -> list[str]:
        service.imported.append(data)
        return [f"version of {data.decode()}"]

    service._import_knxprod_bytes = _import
    return service


def test_a_refused_bundle_is_downloaded_item_by_item() -> None:
    client = _Client()
    service = _service(client)
    added = service.download_online_products(["CI-ROUTER", _POWER_SUPPLY, "CI-DALI"])
    assert client.requests == [
        ["CI-ROUTER", _POWER_SUPPLY, "CI-DALI"],
        ["CI-ROUTER"],
        [_POWER_SUPPLY],
        ["CI-DALI"],
    ]
    assert added == [
        "version of CI-ROUTER",
        f"version of {_POWER_SUPPLY}",
        "version of CI-DALI",
    ]


def test_a_bundle_the_service_accepts_is_one_download() -> None:
    client = _Client()
    service = _service(client)
    service.download_online_products(["CI-ROUTER", "CI-DALI"])
    assert client.requests == [["CI-ROUTER", "CI-DALI"]]
    assert service.imported == [b"CI-ROUTER,CI-DALI"]


def test_other_failures_are_not_repeated() -> None:
    client = _Client(status=500)
    service = _service(client)
    with pytest.raises(OnlineCatalogError):
        service.download_online_products(["CI-ROUTER", _POWER_SUPPLY])
    assert client.requests == [["CI-ROUTER", _POWER_SUPPLY]]


def test_a_refused_single_item_is_reported() -> None:
    class _Refusing(_Client):
        def download_product(self, ids: list[str], languages: list[str]) -> bytes:
            self.requests.append(list(ids))
            raise OnlineCatalogHTTPError("DownloadProduct: 400 Bad Request", 400)

    client = _Refusing()
    with pytest.raises(OnlineCatalogError):
        _service(client).download_online_products(["CI-ROUTER"])
    assert client.requests == [["CI-ROUTER"]]


def test_the_error_keeps_the_http_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(request: Any, timeout: float) -> Any:
        raise urllib.error.HTTPError(
            request.full_url, 400, "Bad Request", {}, io.BytesIO(b"")
        )

    monkeypatch.setattr(online_catalog.urllib.request, "urlopen", _refuse)
    with pytest.raises(OnlineCatalogHTTPError) as caught:
        online_catalog._http_post(
            "https://example.invalid/Download/DownloadProduct", b"{}"
        )
    assert caught.value.http_status == 400
    assert "400 Bad Request" in str(caught.value)


class _Flaky(_Client):
    """Refuses the bundle with the power supply, and also the item ``failing`` on its own."""

    def __init__(self, failing: set[str], error: OnlineCatalogError) -> None:
        super().__init__()
        self.failing = failing
        self.error = error

    def download_product(self, ids: list[str], languages: list[str]) -> bytes:
        if len(ids) == 1 and ids[0] in self.failing:
            self.requests.append(list(ids))
            raise self.error
        return super().download_product(ids, languages)


def test_an_item_failing_on_its_own_does_not_discard_the_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _Flaky({_POWER_SUPPLY}, OnlineCatalogError("DownloadProduct: timed out"))
    service = _service(client)
    added = service.download_online_products(["CI-ROUTER", _POWER_SUPPLY, "CI-DALI"])
    assert client.requests[1:] == [["CI-ROUTER"], [_POWER_SUPPLY], ["CI-DALI"]]
    assert added == ["version of CI-ROUTER", "version of CI-DALI"]
    assert _POWER_SUPPLY in caplog.text


def test_the_error_is_raised_when_no_item_arrives() -> None:
    error = OnlineCatalogHTTPError("DownloadProduct: 400 Bad Request", 400)
    client = _Flaky({"CI-ROUTER", _POWER_SUPPLY}, error)
    with pytest.raises(OnlineCatalogHTTPError) as caught:
        _service(client).download_online_products(["CI-ROUTER", _POWER_SUPPLY])
    assert caught.value is error


def test_items_already_in_the_catalog_are_no_failure() -> None:
    client = _Flaky(
        {"CI-BOGUS"}, OnlineCatalogError("DownloadProduct: 400 Bad Request")
    )
    service = _service(client)
    service._import_knxprod_bytes = lambda data: []  # every version is already there
    assert (
        service.download_online_products([_POWER_SUPPLY, "CI-BOGUS", "CI-ROUTER"]) == []
    )
