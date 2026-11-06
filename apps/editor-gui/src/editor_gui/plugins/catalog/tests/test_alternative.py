"""The alternative catalog's public cache, download contract and registration flow."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest

from editor_gui.plugins.catalog import alternative as alt


def index(data: bytes = b"product") -> dict:
    return {
        "schema": 1,
        "count": 2,
        "generatedAt": "2026-11-05T10:00:00+00:00",
        "entries": [
            {
                "id": "one",
                "manufacturer": "MDT",
                "manufacturerId": "0083",
                "name": "Dimmer",
                "orderNumbers": ["AKD-0401.02"],
                "fileName": "dimmer.knxprod",
                "fileType": "knxprod",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "updated": "2026-10-31",
                "download_url": "/api/download?path=dimmer.knxprod",
                "programs": [
                    {
                        "key": "M-0083_A-1234-03-ABCD",
                        "displayKey": "0083 1234 03 [0701]",
                        "name": "Dimming 4-fold",
                        "manufacturer": "MDT",
                        "manufacturerId": "0083",
                        "applicationNumber": "1234",
                        "version": "03",
                        "maskVersion": "0701",
                        "orderNumbers": ["AKD-0201.02"],
                        "productNames": ["Dimmer four channels"],
                    }
                ],
            },
            {"id": "legacy", "fileType": "vd5"},
        ],
    }


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("editor_gui.settings.config_dir", lambda: tmp_path / "settings")
    monkeypatch.setattr(alt.time, "sleep", lambda _seconds: None)


def client(
    tmp_path: Path, handler, *, credentials: bool = True
) -> alt.AlternativeCatalogClient:
    return alt.AlternativeCatalogClient(
        tmp_path,
        settings=alt.AlternativeSettings(
            "https://catalog.example",
            "user" if credentials else "",
            "secret" if credentials else "",
        ),
        transport=httpx.MockTransport(handler),
    )


def test_public_gzip_etag_and_offline_search(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def serve(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert "authorization" not in request.headers
        assert request.headers["accept-encoding"] == "gzip"
        if len(requests) == 2:
            assert request.headers["if-none-match"] == '"v1"'
            return httpx.Response(304)
        return httpx.Response(
            200,
            content=gzip.compress(json.dumps(index()).encode()),
            headers={"Content-Encoding": "gzip", "ETag": '"v1"'},
        )

    c = client(tmp_path, serve, credentials=False)
    assert c.refresh()["count"] == 1
    assert c.refresh()["count"] == 1
    for query in (
        "MDT 1234 03",
        "AKD040102",
        "AKD-0201.02",
        "four channels",
        "0701",
        "dimmer.knxprod",
    ):
        assert c.search(query)[1] == 1
    assert c.search("legacy")[1] == 0
    assert c.search("missing")[1] == 0
    c.close()
    offline = client(tmp_path, lambda r: pytest.fail("offline search touched network"))
    offline.load_cache()
    assert offline.search("mdt")[1] == 1
    assert offline.search("", 1, 1) == ([], 1)
    offline.close()


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, text="broken"),
        httpx.Response(200, json={"schema": 99, "entries": []}),
    ],
)
def test_failed_refresh_preserves_cache(
    tmp_path: Path, response: httpx.Response
) -> None:
    replies = iter([httpx.Response(200, json=index()), response])
    c = client(tmp_path, lambda r: next(replies))
    c.refresh()
    with pytest.raises(alt.AlternativeCatalogError):
        c.refresh()
    assert c.search("Dimmer")[1] == 1
    assert c.status()["error"]
    c.close()


@pytest.mark.parametrize(
    "url",
    [
        "http://catalog.example",
        "https://user:secret@catalog.example",
        "https://catalog.example?x=1",
        "https://catalog.example/#secret",
    ],
)
def test_server_requires_https_origin(url: str) -> None:
    with pytest.raises(ValueError):
        alt.server_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "//other.example/api/download",
        "https://other.example/api/download",
        "/api/download#secret",
        "/api/other",
        "/\\other.example/api/download",
    ],
)
def test_index_cannot_redirect_authenticated_downloads(url: str) -> None:
    raw = index()
    raw["entries"][0]["download_url"] = url
    with pytest.raises(ValueError):
        alt.parse_index(raw)


def test_settings_are_private_and_server_change_drops_secrets_and_cache(
    tmp_path: Path,
) -> None:
    c = client(tmp_path, lambda r: httpx.Response(200, json=index()))
    c.configure("https://catalog.example", "user", "password")
    c.refresh()
    c.configure("https://catalog.example", "user")
    assert c.settings.password == "password"
    assert "password" not in repr(c.settings)
    assert "password" not in json.dumps(c.status()).replace("has_password", "")
    settings = tmp_path / "settings" / "alternative_catalog.json"
    # Readable by the user only (POSIX file modes; Windows has none).
    if os.name != "nt":
        assert settings.stat().st_mode & 0o777 == 0o600
    c.configure("https://second.example", "user")
    assert c.settings.password == "" and c.search()[1] == 0
    assert not c.status()["cached"]
    c.close()


def test_download_renews_invalid_token_once_and_verifies_file(tmp_path: Path) -> None:
    seen: list[str] = []

    def serve(r: httpx.Request) -> httpx.Response:
        seen.append(r.url.path)
        if r.url.path.endswith("index.json"):
            return httpx.Response(200, json=index())
        if r.url.path.endswith("token"):
            assert json.loads(r.content) == {"user": "user", "password": "secret"}
            return httpx.Response(
                200,
                json={
                    "token": f"token{len(seen)}",
                    "tokenType": "Bearer",
                    "expiresIn": 43200,
                },
            )
        assert r.headers["authorization"].startswith("Bearer token")
        if seen.count("/api/download") == 1:
            return httpx.Response(401, json={"code": "INVALID_TOKEN"})
        return httpx.Response(200, content=b"product")

    c = client(tmp_path, serve)
    c.refresh()
    assert c.download("one") == b"product"
    assert seen == [
        "/api/catalog/index.json",
        "/api/auth/token",
        "/api/download",
        "/api/auth/token",
        "/api/download",
    ]
    c.close()


@pytest.mark.parametrize(
    "status,code", [(403, ""), (404, ""), (503, ""), (401, "INVALID_TOKEN")]
)
def test_download_errors_do_not_loop(tmp_path: Path, status: int, code: str) -> None:
    seen: list[str] = []

    def serve(r: httpx.Request) -> httpx.Response:
        seen.append(r.url.path)
        if r.url.path.endswith("index.json"):
            return httpx.Response(200, json=index())
        if r.url.path.endswith("token"):
            return httpx.Response(
                200, json={"token": "t", "tokenType": "Bearer", "expiresIn": 43200}
            )
        return httpx.Response(status, json={"code": code})

    c = client(tmp_path, serve)
    c.refresh()
    with pytest.raises(alt.AlternativeCatalogError):
        c.download("one")
    assert seen.count("/api/download") == (2 if status == 401 else 1)
    c.close()


def test_failed_token_renewal_stops_download(tmp_path: Path) -> None:
    tokens = 0
    downloads = 0

    def serve(r: httpx.Request) -> httpx.Response:
        nonlocal tokens, downloads
        if r.url.path.endswith("index.json"):
            return httpx.Response(200, json=index())
        if r.url.path.endswith("token"):
            tokens += 1
            return (
                httpx.Response(
                    200, json={"token": "t", "tokenType": "Bearer", "expiresIn": 43200}
                )
                if tokens == 1
                else httpx.Response(401)
            )
        downloads += 1
        return httpx.Response(401, json={"code": "INVALID_TOKEN"})

    c = client(tmp_path, serve)
    c.refresh()
    with pytest.raises(alt.AlternativeCatalogError):
        c.download("one")
    assert tokens == 2 and downloads == 1
    c.close()


@pytest.mark.parametrize("content", [b"wrong!!", b"short", b"long product"])
def test_corrupt_download_is_not_accepted(tmp_path: Path, content: bytes) -> None:
    def serve(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("index.json"):
            return httpx.Response(200, json=index())
        if r.url.path.endswith("token"):
            return httpx.Response(
                200, json={"token": "t", "tokenType": "Bearer", "expiresIn": 43200}
            )
        return httpx.Response(200, content=content)

    c = client(tmp_path, serve)
    c.refresh()
    with pytest.raises(alt.AlternativeCatalogError, match="SHA-256"):
        c.download("one")
    c.close()


@pytest.mark.parametrize("code", ["DOWNLOAD_DAILY_LIMIT", "RATE_LIMIT"])
def test_retry_after_blocks_repeated_requests(tmp_path: Path, code: str) -> None:
    calls: list[str] = []

    def serve(r: httpx.Request) -> httpx.Response:
        calls.append(r.url.path)
        if r.url.path.endswith("index.json"):
            return httpx.Response(200, json=index())
        if r.url.path.endswith("token"):
            return httpx.Response(
                200, json={"token": "t", "tokenType": "Bearer", "expiresIn": 43200}
            )
        return httpx.Response(
            429,
            headers={"Retry-After": "3600"},
            json={"code": code, "resetAt": "2026-11-06T00:00:00+01:00"},
        )

    c = client(tmp_path, serve)
    c.refresh()
    with pytest.raises(alt.AlternativeCatalogError) as exc:
        c.download("one")
    assert exc.value.retry_after >= 3600 and exc.value.code == code
    before = len(calls)
    with pytest.raises(alt.AlternativeCatalogError):
        c.download("one")
    assert len(calls) == before
    if code == "DOWNLOAD_DAILY_LIMIT":
        assert c.refresh()["count"] == 1
    c.close()


def test_registration_cookie_csrf_verify_resend_and_resume(tmp_path: Path) -> None:
    seen: list[str] = []
    registered = False

    def serve(r: httpx.Request) -> httpx.Response:
        nonlocal registered
        seen.append(r.url.path)
        if r.url.path == "/api/me":
            return httpx.Response(
                200,
                headers={"Set-Cookie": "session=one; Path=/; Secure"},
                json={
                    "csrfToken": "csrf",
                    "registrationPending": {"status": "unverified"},
                },
            )
        assert "session=one" in r.headers["cookie"]
        assert r.headers["x-csrf-token"] == "csrf"
        body = json.loads(r.content)
        if r.url.path.endswith("register"):
            assert body == {
                "user": "newuser",
                "email": "user@example.com",
                "password": "NewPassword1!",
                "website": "",
            }
            registered = True
        if r.url.path.endswith("verify"):
            assert registered and body == {"code": "123456"}
        if r.url.path.endswith("login"):
            assert body == {"user": "newuser", "password": "NewPassword1!"}
        return httpx.Response(200, json={"message": "ok"})

    c = client(tmp_path, serve)
    assert (
        c.register("newuser", "user@example.com", "NewPassword1!")["status"]
        == "unverified"
    )
    assert c.settings.user == "newuser"
    assert c.verify_registration("123456")["status"] == "pending"
    assert "/api/auth/login" not in seen
    assert c.resend_registration_code()["status"] == "unverified"
    c.close()
    resumed = alt.AlternativeCatalogClient(
        tmp_path, transport=httpx.MockTransport(serve)
    )
    assert resumed.resume_registration()["status"] == "unverified"
    assert resumed.verify_registration("123456")["status"] == "pending"
    assert seen.count("/api/auth/login") == 1
    resumed.close()


def test_registration_failure_redacts_new_password(tmp_path: Path) -> None:
    def serve(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("me"):
            return httpx.Response(200, json={"csrfToken": "csrf"})
        return httpx.Response(400, json={"error": "bad NewPassword1!"})

    c = client(tmp_path, serve)
    with pytest.raises(alt.AlternativeCatalogError) as exc:
        c.register("newuser", "user@example.com", "NewPassword1!")
    assert "NewPassword1!" not in str(exc.value)
    assert c.settings.user == "user"
    c.close()


def test_duplicate_file_ids_rejected() -> None:
    raw = index()
    raw["entries"][1] = copy.deepcopy(raw["entries"][0])
    with pytest.raises(ValueError):
        alt.parse_index(raw)


@pytest.mark.parametrize(
    "raw,display",
    [
        ("23", "2.3"),
        ("10", "1.0"),
        ("2A", "2.10"),
        ("0F", "0.15"),
        ("FF", "15.15"),
        ("unknown", "unknown"),
    ],
)
def test_hexadecimal_program_version(raw: str, display: str) -> None:
    assert alt.program_version_text(raw) == display


def test_version_display_is_searchable_and_raw_version_preserved(
    tmp_path: Path,
) -> None:
    raw = index()
    raw["entries"][0]["programs"][0]["version"] = "2A"
    c = client(tmp_path, lambda r: httpx.Response(200, json=raw))
    c.refresh()
    entries, total = c.search("2.10")
    assert total == 1
    program = entries[0].as_dict()["programs"][0]
    assert program["version"] == "2A" and program["version_text"] == "2.10"
    c.close()


def test_invalid_saved_server_does_not_move_credentials_to_default(
    tmp_path: Path,
) -> None:
    alt.save_settings(
        "alternative_catalog",
        {
            "base_url": "http://old.example",
            "user": "private",
            "password": "private-password",
        },
    )
    assert alt.AlternativeSettings.load() == alt.AlternativeSettings()


def test_manufacturer_matches_precede_incidental_hex_ids(tmp_path: Path) -> None:
    raw = index()
    unrelated = raw["entries"][0]
    unrelated["programs"][0]["key"] = "M-0083_A-1234-03-ABBA"
    manufacturer = copy.deepcopy(unrelated)
    manufacturer.update(id="abb", manufacturer="ABB")
    raw["entries"][1] = manufacturer
    c = client(tmp_path, lambda r: httpx.Response(200, json=raw))
    c.refresh()
    entries, total = c.search("abb", limit=1)
    assert total == 2 and entries[0].manufacturer == "ABB"
    c.close()


def test_search_does_not_join_adjacent_fields(tmp_path: Path) -> None:
    raw = index()
    raw["entries"][0].update(manufacturer="AA", manufacturerId="BB")
    c = client(tmp_path, lambda r: httpx.Response(200, json=raw))
    c.refresh()
    assert c.search("AABB")[1] == 0
    c.close()


def large_index(count: int = 1205) -> dict:
    raw = index()
    template = raw["entries"][0]
    raw["entries"] = [
        {
            **template,
            "id": f"product-{i}",
            "name": f"Product {i}\nSecond line" if i % 2 else f"Product {i}",
            "manufacturer": "MDT\nControls" if i % 2 else "MDT",
        }
        for i in range(count)
    ]
    raw["count"] = count
    return raw


def test_all_local_matches_and_paginated_access(tmp_path: Path) -> None:
    c = client(tmp_path, lambda r: httpx.Response(200, json=large_index()))
    c.refresh()
    c.close()
    entries, total = c.search("product")
    assert len(entries) == total == 1205
    assert entries[-1].id == "product-1204"
    pages = [c.search("product", limit=500, offset=n)[0] for n in (0, 500, 1000)]
    assert [entry for page in pages for entry in page] == entries
    assert c.search("product", offset=1200) == (entries[1200:], total)
    assert c.search("product", offset=total) == ([], total)


@pytest.mark.parametrize("limit,offset", [(0, 0), (-1, 0), (1001, 0), (None, -1)])
def test_invalid_search_page(tmp_path: Path, limit: int | None, offset: int) -> None:
    c = client(tmp_path, lambda r: pytest.fail("search touched network"))
    try:
        with pytest.raises(ValueError):
            c.search(limit=limit, offset=offset)
    finally:
        c.close()
